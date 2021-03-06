%% -*- erlang-indent-level: 2 -*-
%%------------------------------------------------------------------------------
-module(concolic_scheduler).
-behaviour(gen_server).

%% External exports
-export([start/2, stop/1, initial_execution/4, request_input/1,
         store_execution/5]).

%% gen_server callbacks
-export([init/1, terminate/2, code_change/3, handle_info/2,
         handle_call/3, handle_cast/2]).

-type call()  :: 'request_input'
               | {'init_execution', string(), concolic_analyzer:traces(), [concolic_symbolic:mapping()]}
               | {'store_execution', reference(), string(), concolic_analyzer:traces(), [concolic_symbolic:mapping()]}.
-type cast()  :: 'stop'.
-type reply() :: 'ok'
               | 'empty'
               | {reference(), [term()]}.

%% gen_server state datatype
-record(state, {
  queue,
  info,
  python,
  depth
}).
-type state() :: #state{}.

%% ============================================================================
%% External exports
%% ============================================================================

%% Start the Scheduler
-spec start(string(), integer()) -> pid() | no_return().

start(Python, Depth) ->
  case gen_server:start_link(?MODULE, [Python, Depth], []) of
    {ok, Scheduler} -> Scheduler;
    {error, R} -> exit({error_starting_scheduler, R})
  end.

%% Store the information of the 1st concolic execution
%% (that will be used as a guide)
-spec initial_execution(pid(), string(), concolic_analyzer:traces(), [concolic_symbolic:mapping()]) -> ok.

initial_execution(Scheduler, DataDir, Traces, Mapping) ->
  gen_server:call(Scheduler, {init_execution, DataDir, Traces, Mapping}).

%% Store the information of a concolic execution
-spec store_execution(pid(), reference(), string(), concolic_analyzer:traces(), [concolic_symbolic:mapping()]) -> ok.

store_execution(Scheduler, Ref, DataDir, Traces, Mapping) ->
  gen_server:call(Scheduler, {store_execution, Ref, DataDir, Traces, Mapping}).

%% Request a new Input vertex for concolic execution
-spec request_input(pid()) -> {reference(), [term()]} | 'empty'.

request_input(Scheduler) ->
  gen_server:call(Scheduler, request_input, 200000).

%% Stop the Scheduler
-spec stop(pid()) -> ok.

stop(Scheduler) ->
  gen_server:cast(Scheduler, stop).

%% ============================================================================
%% gen_server callbacks
%% ============================================================================

%% ------------------------------------------------------------------
%% gen_server callback : init/1
%% ------------------------------------------------------------------
-spec init([string() | integer(), ...]) -> {ok, state()}.

init([Python, Depth]) ->
  Q = queue:new(),
  I = ets:new(?MODULE, [ordered_set, protected]),
  {ok, #state{queue = Q, info = I, python = Python, depth = Depth}}.

%% ------------------------------------------------------------------
%% gen_server callback : terminate/2
%% ------------------------------------------------------------------
-spec terminate(term(), state()) -> ok.

terminate(_Reason, #state{info = I}) ->
  ets:delete(I),
  ok.

%% ------------------------------------------------------------------
%% gen_server callback : code_change/3
%% ------------------------------------------------------------------
-spec code_change(term(), state(), term()) -> {ok, state()}.
  
code_change(_OldVsn, State, _Extra) ->
  {ok, State}.  %% No change planned.

%% ------------------------------------------------------------------
%% gen_server callback : handle_info/2
%% ------------------------------------------------------------------
-spec handle_info(term(), state()) -> {noreply, state()}.

handle_info(Msg, State) ->
  %% Just outputting unexpected messages for now
  io:format("[~s]: Unexpected message ~p~n", [?MODULE, Msg]),
  {noreply, State}.

%% ------------------------------------------------------------------
%% gen_server callback : handle_call/3
%% ------------------------------------------------------------------
-spec handle_call(call(), {pid(), reference()}, state()) -> {reply, reply(), state()}.

handle_call({'init_execution', DataDir, Traces, Mapping}, _From, S=#state{queue = Q, info = I}) ->
  R = make_ref(),
%  io:format("[~s]: Init = ~p~n", [?MODULE, R]),
  Q1 = queue:in(R, Q),
  %% SIMPLIFICATION : Assume Sequential Execution
  [{_, [V]}] = concolic_analyzer:get_execution_vertices(Traces),
  Data = create_info(1, length(V), DataDir, Traces, Mapping),
  ets:insert(I, {R, Data}),
  {reply, ok, S#state{queue = Q1}};

handle_call({'store_execution', Ref, DataDir, Traces, Mapping}, _From, S=#state{queue = Q, info = I}) ->
  [{Ref, Ps}] = ets:lookup(I, Ref),
  %% SIMPLIFICATION : Assume Sequential Execution
  [{_, [V]}] = concolic_analyzer:get_execution_vertices(Traces),
  L = length(V),
  case next_constraint(Ps) > L of
    true ->
%      io:format("[~s]: Wont queue ~p (~w > ~w)~n", [?MODULE, Ref, next_constraint(Ps), L]),
      concolic_analyzer:clear_and_delete_dir(DataDir),
      {reply, ok, S};
    false ->
      Ps1 = update_partial_info(Ps, L, DataDir, Traces, Mapping),
      ets:insert(I, {Ref, Ps1}),
      Q1 = queue:in(Ref, Q),
      {reply, ok, S#state{queue = Q1}}
  end;

handle_call('request_input', _From, S=#state{queue = Q, info = I, python = P, depth = D}) ->
  case generate_testcase(Q, I, P, D) of
    {empty, Q1} ->
      {reply, empty, S#state{queue = Q1}};
    {ok, {R, Inp}, Q1} ->
      {reply, {R, Inp}, S#state{queue = Q1}}
  end.

%% ------------------------------------------------------------------
%% gen_server callback : handle_cast/2
%% ------------------------------------------------------------------
-spec handle_cast(cast(), state()) -> {stop, normal, state()}.

handle_cast(stop, State) ->
  {stop, normal, State}.

%% ============================================================================
%% Internal functions
%% ============================================================================

generate_testcase(Q, I, P, D) ->
  case expand_state(Q, I, P, D) of
    {error, Q1} -> generate_testcase(Q1, I, P, D);
    X -> X
  end.

expand_state(Q, I, P, D) ->
  case queue:out(Q) of
    {{value, R}, Q1} ->
      [{R, Ps}] = ets:lookup(I, R),
      %% SIMPLIFICATION : Assume Sequential Execution
      [File] = proplists:get_value(node(), traces(Ps)),
      X = next_constraint(Ps),
%      io:format("[~s]: Try to expand ~p at ~w~n", [?MODULE, R, X]),
      case python:solve(File, X, mapping(Ps), P) of
        error ->
%          io:format("[~s]: Failed~n", [?MODULE]),
          Q2 = requeue_state(Ps, Q1, R, I, D),
          {error, Q2};
        {ok, Inp} ->
          R1 = make_ref(),
%          io:format("[~s]: New Inp = ~p~n", [?MODULE, R1]),
          ets:insert(I, {R1, create_partial_info(X+1)}),
          Q2 = requeue_state(Ps, Q1, R, I, D),
          {ok, {R1, Inp}, Q2}
      end;
    {empty, Q} = E -> E;
    X -> throw(X)
  end.

requeue_state(Ps, Q, R, I, D) ->
%  io:format("[~s]: Will try to requeue ~p~n", [?MODULE, R]),
  case increase_next_constraint(Ps, D) of
    false ->
%      io:format("[~s]: Failed~n", [?MODULE]),
      DataDir = datadir(Ps),
      concolic_analyzer:clear_and_delete_dir(DataDir),
      Q;
    {ok, Ps1} ->
%      io:format("[~s]: Done~n", [?MODULE]),
      Q1 = queue:in(R, Q),
      ets:insert(I, {R, Ps1}),
      Q1
  end.

%% ------------------------------------------------------------------
%% Functions that handle the info stored in the ETS table
%% Info structure
%% --------------
%% {No of constraint to negate :: integer(),
%%  Length of execution path :: integer(),
%%  DataDir :: string(),
%%  Traces :: concolic_analyzer:traces(),
%%  Mapping :: [concolic_symbolic:mapping()]}
%% ------------------------------------------------------------------

create_partial_info(I) ->
  [{'next_constraint', I}].

update_partial_info(Ps, L, DataDir, Ts, Ms) ->
  [{'path_length', L}, {'datadir', DataDir}, {'traces', Ts}, {'mapping', Ms} | Ps].

increase_next_constraint(Ps, Depth) ->
  X = next_constraint(Ps) + 1,
  L = path_length(Ps),
  case X > L orelse X > Depth of
    true -> false;
    false -> {ok, replace_property('next_constraint', X, Ps)}
  end.

replace_property(Key, Value, Ps) -> replace_property(Key, Value, Ps, []).

replace_property(Key, Value, [{Key, _V}|Ps], Acc) ->
  Acc ++ [{Key, Value}|Ps];
replace_property(Key, Value, [{_K, _V}=P|Ps], Acc) ->
  replace_property(Key, Value, Ps, [P|Acc]).

create_info(I, L, DataDir, Ts, Ms) ->
  [{'next_constraint', I}, {'path_length', L}, {'datadir', DataDir}, {'traces', Ts}, {'mapping', Ms}].

next_constraint(Ps) -> proplists:get_value('next_constraint', Ps).

path_length(Ps) -> proplists:get_value('path_length', Ps).

datadir(Ps) -> proplists:get_value('datadir', Ps).

traces(Ps) -> proplists:get_value('traces', Ps).

mapping(Ps) -> proplists:get_value('mapping', Ps).
