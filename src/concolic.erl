%% -*- erlang-indent-level: 2 -*-
%%------------------------------------------------------------------------------
-module(concolic).

-behaviour(gen_server).

%% External exports
-export([init_server/6, node_servers/2, send_clogs/2,
         send_error_report/3, send_return/2, send_tlogs/2, send_mapping/2]).

%% gen_server callbacks
-export([init/1, terminate/2, code_change/3,
         handle_call/3, handle_cast/2, handle_info/2]).

%% exported types
-export_type([exec_info/0, servers/0]).

%% type declarations
-type call()      :: {'int_return', term()}
                   | {'mapping', [concolic_symbolic:mapping()]}
                   | {'node_servers', node()}
                   | {'error_report', pid(), term()}
                   | {'clogs', concolic_cserver:clogs()}
                   | {'tlogs', concolic_tserver:tlogs()}.
-type exec_info() :: orddict:orddict().
-type info()      :: {'EXIT', pid(), 'normal' | term()}.
-type reply()     :: 'ok' | 'proc_mismatch' | servers().
-type servers()   :: {pid(), pid()}.
-type spid()      :: {node(), pid()}.
%% gen_server state datatype
-record(state, {
  coord    :: pid(),                     %% Pid of the Coordinator Process
  coredir  :: string(),                  %% Directory to store .core files
  tracedir :: string(),                  %% Directory to store trace files
  depth    :: integer(),                 %% Number of constraints to log
  cpids    :: [spid()],                  %% Proplist of CodeServers
  tpids    :: [spid()],                  %% Proplist of TraceServers
  results  :: exec_info(),               %% Info about the concolic execution
  int      :: pid() | 'ok',              %% First interpreted process that returns its result
  error    :: {atom(), term()} | 'false' %% Flag for when errors occur
}).
-type state() :: #state{}.

%% ============================================================================
%% External exports
%% ============================================================================

%% Initialize the Concolic Server
-spec init_server(atom(), atom(), [term()], string(), string(), integer()) -> pid() | term().

init_server(M, F, As, CoreDir, TraceDir, Depth) ->
  Args = [M, F, As, CoreDir, TraceDir, self(), Depth],
  case gen_server:start_link(?MODULE, Args, []) of
    {ok, Server} -> Server;
    {error, _Reason} = R -> R
  end.
  
%% Send the result of the first interpreted process
-spec send_return(pid(), term()) -> 'ok' | 'proc_mismatch'.

send_return(ConcServer, Return) ->
  gen_server:call(ConcServer, {int_return, Return}).

%% Send the mapping of the concrete to symbolic values
-spec send_mapping(pid(), [concolic_symbolic:mapping()]) -> 'ok' | 'proc_mismatch'.

send_mapping(ConcServer, Mapping) ->
  gen_server:call(ConcServer, {mapping, Mapping}).
  
%% Send the report of a Runtime Error
-spec send_error_report(pid(), pid(), term()) -> 'ok'.

send_error_report(ConcServer, Who, Error) ->
  gen_server:call(ConcServer, {error_report, Who, Error}).
 
%% Send the logs of a CodeServer
-spec send_clogs(pid(), concolic_cserver:clogs()) -> 'ok'.

send_clogs(ConcServer, Logs) ->
  gen_server:call(ConcServer, {clogs, Logs}).
  
%% Send the logs of a TraceServer
-spec send_tlogs(pid(), concolic_tserver:tlogs()) -> 'ok'.

send_tlogs(ConcServer, Logs) ->
  gen_server:call(ConcServer, {tlogs, Logs}).
  
%% Request the CodeServer and TraceServer of a specific node
-spec node_servers(pid(), node()) -> servers() | 'error'.

node_servers(ConcServer, Node) ->
  gen_server:call(ConcServer, {node_servers, Node}).
  
%% ============================================================================
%% gen_server callbacks
%% ============================================================================

%% ------------------------------------------------------------------
%% gen_server callback : init/1
%% ------------------------------------------------------------------
-spec init([atom() | string() | pid() | [term()] | integer(), ...]) -> {'ok', state()}.

init([M, F, As, CoreDir, TraceDir, Coord, Depth]) ->
  process_flag(trap_exit, true),
  Node = node(),
  CodeServer = concolic_cserver:init_codeserver(CoreDir, self()),
  TraceServer = concolic_tserver:init_traceserver(TraceDir, self(), Depth),
  Ipid = concolic_eval:i(M, F, As, CodeServer, TraceServer),
  InitState = #state{
    coord = Coord,
    coredir = CoreDir,
    tracedir = TraceDir,
    depth = Depth,
    cpids = [{Node, CodeServer}],
    tpids = [{Node, TraceServer}],
    results = orddict:new(),
    int = Ipid,
    error = false
  },
  {ok, InitState}.

%% ------------------------------------------------------------------
%% gen_server callback : terminate/2
%% ------------------------------------------------------------------
-spec terminate(term(), state()) -> 'ok'.

terminate(_Reason, State) ->
  Coord = State#state.coord,
  Results = State#state.results,
  CoreDir = State#state.coredir,
  Error = State#state.error,
  %% Directory will only be deleted if it's empty
  _ = file:del_dir(filename:absname(CoreDir)),
  case Error of
    {internalc, Node} ->
      Coord ! {self(), {'internal_codeserver_error', Node, Results}},
      ok;
    {internalt, Node} ->
      Coord ! {self(), {'internal_traceserver_error', Node, Results}},
      ok;
    {runtime, Node} ->
      Coord ! {self(), {'runtime_error', Node, Results}},
      ok;
    {internal, Error} ->
      exit(Error);
    false ->
      Coord ! {self(), {ok, node(), Results}},
      ok
  end.

%% ------------------------------------------------------------------
%% gen_server callback : code_change/3
%% ------------------------------------------------------------------
-spec code_change(term(), state(), term()) -> {'ok', state()}.

code_change(_OldVsn, State, _Extra) ->
  {ok, State}.  %% No change planned.

%% ------------------------------------------------------------------
%% gen_server callback : handle_call/3
%% ------------------------------------------------------------------
-spec handle_call(call(), {pid(), reference()}, state()) ->
  {'reply', reply(), state()} | {'stop', 'error', 'normal', state()}.

%% Log the mapping of concrete to symbolic values
%% Call Request : {mapping, Mapping}
%% Reply : ok | proc_mismatch
handle_call({mapping, Mapping}, {From, _FromTag}, State) ->
  Ipid = State#state.int,
  Res = State#state.results,
  FromPid = 
    case is_atom(From) of
     true ->  whereis(From);  %% Always this will be a local process
     false -> From
    end,
  case Ipid =:= FromPid of
    true  ->
      Node = node(FromPid),
      List = [{mapping, Mapping}],
      NRes = orddict:append_list(Node, List, Res),
      {reply, ok, State#state{results = NRes}};
    false ->
      {reply, proc_mismatch, State}
  end;
%% Log the result of the first spawned process
%% Call Request : {int_return, Ret}
%% Reply : ok | proc_mismatch
handle_call({int_return, Return}, {From, _FromTag}, State) ->
  Ipid = State#state.int,
  Res = State#state.results,
  FromPid = 
    case is_atom(From) of
     true ->  whereis(From);  %% Always this will be a local process
     false -> From
    end,
  case Ipid =:= FromPid of
    true  ->
      Node = node(FromPid),
      List = [{result, Return}],
      NRes = orddict:append_list(Node, List, Res),
      {reply, ok, State#state{results = NRes, int = ok}};
    false ->
      {reply, proc_mismatch, State}
  end;
%% Log a runtime error
%% Call Request : {error_report, Who, Error}
%% Reply : ok
handle_call({error_report, Who, Error}, {From, _FromTag}, State) ->
  CPids = State#state.cpids,
  TPids = State#state.tpids,
  Res = State#state.results,
  {Node, From} = NF = lists:keyfind(From, 2, TPids),  %% Process will never be registered
  %% Store Runtime Error's Info
  NRes = orddict:append(Node, {runtime_error, {Node, Who, Error}}, Res),
  %% Shutdown execution tree
  force_terminate(CPids, TPids -- [NF]),
  {reply, ok, State#state{results = NRes, int = ok, error = {runtime, Node}}};
%% Handle a request for the servers of a specific node
%% Call Request : {node_servers, Node}
%% Reply : Servers | error
handle_call({node_servers, Node}, _From, State) ->
  CPids = State#state.cpids,
  TPids = State#state.tpids,
  CoreDir = State#state.coredir,
  TraceDir = State#state.tracedir,
  Depth = State#state.depth,
  case node_monitored(Node, CPids, TPids) of
    %% Servers are already up on Node
    {true, Servers} ->
      {reply, Servers, State};
    false ->
      %% Spawn servers on Node
      case remote_spawn_servers(Node, CoreDir, TraceDir, self(), Depth) of
        {ok, {CodeServer, TraceServer} = Servers} ->
          NCPids = [{Node, CodeServer}|CPids],
          NTPids = [{Node, TraceServer}|TPids],
          {reply, Servers, State#state{cpids = NCPids, tpids = NTPids}};
        error ->
          %% Error while spawning servers so shutdown processes and
          %% terminate immediately with internal error
          force_terminate(CPids, TPids),
          {stop, error, normal, State#state{error = {internal, init_servers}}}
      end
  end;
%% Store the logs of a CodeServer
%% Call Request : {clogs, Logs}
%% Reply : ok
handle_call({clogs, _Logs} = Clogs, {From, _FromTag}, State) ->
  CPids = State#state.cpids,
  Res = State#state.results,
  {Node, From} = lists:keyfind(From, 2, CPids),  %% Process will never be registered
  NRes = orddict:append(Node, Clogs, Res),
  {reply, ok, State#state{results = NRes}};
%% Store the logs of a TraceServer
%% Call Request : {tlogs, Logs}
%% Reply : ok
handle_call({tlogs, _Logs} = Tlogs, {From, _FromTag}, State) ->
  TPids = State#state.tpids,
  Res = State#state.results,
  {Node, From} = lists:keyfind(From, 2, TPids),  %% Process will never be registered
  NRes = orddict:append(Node, Tlogs, Res),
  {reply, ok, State#state{results = NRes}}.

%% ------------------------------------------------------------------
%% gen_server callback : handle_cast/2
%% ------------------------------------------------------------------
-spec handle_cast(term(), state()) -> {'noreply', state()}.

handle_cast(_Msg, State) ->
  {noreply, State}.  %% Nothing planned for cast requests

%% ------------------------------------------------------------------
%% gen_server callback : handle_info/2
%% ------------------------------------------------------------------
-spec handle_info(info(), state()) -> {'noreply', state()}
                                    | {'stop', 'normal', state()}.

%% When a CodeServer or TraceServer have exited normally
%% Msg : {'EXIT', Who, normal}
handle_info({'EXIT', Who, normal}, State) ->
  CPids = State#state.cpids,
  TPids = State#state.tpids,
  case locate(Who, CPids, TPids) of
    %% A Codeserver exited
    {true, codeserver, Node} ->
      NCPids = CPids -- [{Node, Who}],
      NState = State#state{cpids = NCPids},
      case execution_completed(NState) of
        true  -> {stop, normal, NState};
        false -> {noreply, NState}
      end;
    %% A Traceserver exited
    {true, traceserver, Node} ->
      NTPids = TPids -- [{Node, Who}],
      NState = State#state{tpids = NTPids},
      case execution_completed(NState) of
        %% If all processes have finished then terminate
        true  ->
          {stop, normal, NState};
        %% else wait for the rest
        false ->
          {Node, CodeServer} = lists:keyfind(Node, 1, CPids),
          concolic_cserver:terminate(CodeServer),
          {noreply, NState}
      end;
    false ->
      {noreply, State}
  end;
%% When a CodeServer or TraceServer have exited with exceptions
%% Msg : {'EXIT', Who, Reason}
handle_info({'EXIT', Who, Reason}, State) ->
  CPids = State#state.cpids,
  TPids = State#state.tpids,
  Res = State#state.results,
  %% locate the server that exited and shutdown the execution tree
  case locate(Who, CPids, TPids) of
    {true, codeserver, Node} ->
      NCPids = CPids -- [{Node, Who}],
      force_terminate(NCPids, TPids),
      NRes = orddict:append(Node, {codeserver_error, Reason}, Res),
      {noreply, State#state{cpids = NCPids, results = NRes,
                            error = {internalc, Node}, int = ok}};
    {true, traceserver, Node} ->
      NTPids = TPids -- [{Node, Who}],
      force_terminate(CPids, NTPids),
      NRes = orddict:append(Node, {traceserver_error, Reason}, Res),
      {noreply, State#state{tpids = NTPids, results = NRes,
                            error = {internalt, Node}, int = ok}};
    false ->
      {noreply, State}
  end.


%% ============================================================================
%% Internal functions
%% ============================================================================

%% Determine whether the concolic execution has ended or not
-spec execution_completed(state()) -> boolean().
  
execution_completed(#state{cpids = CPids, tpids = TPids, int = Int}) ->
  case {CPids, TPids, Int} of
    {[], [], ok} -> true;
    _ -> false
  end.

%% Send terminate requests to all the live CodeServers and TraceServers
-spec force_terminate([spid()], [spid()]) -> ok.

force_terminate(CPids, TPids) ->
  lists:foreach(fun({_Node, P}) -> concolic_cserver:terminate(P) end, CPids),
  lists:foreach(fun({_Node, P}) -> concolic_tserver:terminate(P) end, TPids).

%% Locate and return the node info of a CodeServer or TraceServer
-spec locate(pid(), [spid()], [spid()]) -> {'true', 'codeserver', node()}
                                         | {'true', 'traceserver', node()}
                                         | 'false'.
  
locate(Who, CPids, TPids) ->
  case lists:keyfind(Who, 2, CPids) of
    {Node, Who} ->
      {true, codeserver, Node};
    false ->
      case lists:keyfind(Who, 2, TPids) of
        {Node, Who} ->
          {true, traceserver, Node};
        false ->
          false
      end
  end.
  
%% Check whether a node has beend assigned a TraceServer and a CodeServer
-spec node_monitored(node(), [spid()], [spid()]) -> {'true', servers()} | 'false'.

node_monitored(Node, CPids, TPids) ->
  C = lists:keyfind(Node, 1, CPids),
  T = lists:keyfind(Node, 1, TPids),
  case {C, T} of
    {{Node, CodeServer}, {Node, TraceServer}} ->
      {true, {CodeServer, TraceServer}};
    {false, false} ->
      false
  end.
  
%% Spawn a TraceServer and a CodeServer at a remote node
-spec remote_spawn_servers(node(), string(), string(), pid(), integer()) -> {'ok', servers()} | 'error'.
  
remote_spawn_servers(Node, CoreDir, TraceDir, Super, Depth) ->
  Me = self(),
  F = fun() ->
    process_flag(trap_exit, true),
    CodeServer = concolic_cserver:init_codeserver(CoreDir, Super),
    TraceServer = concolic_tserver:init_traceserver(TraceDir, Super, Depth),
    Me ! {self(), {CodeServer, TraceServer}}
  end,
  P = spawn_link(Node, F),
  receive
    {P, Servers} ->
      {ok, Servers};
    {'EXIT', P, _Error} ->
      error
  end.
