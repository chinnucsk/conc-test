%% -*- erlang-indent-level: 2 -*-
%%------------------------------------------------------------------------------
-module(concolic_cserver).
-behaviour(gen_server).

%% External exports
-export([init_codeserver/2, terminate/1, load/2, mfa_spec/2, return_spec/3, spec_finder/2, module_attributes/2]).

%% gen_server callbacks
-export([init/1, terminate/2, code_change/3, handle_info/2,
         handle_call/3, handle_cast/2]).
  
%% exported types
-export_type([clogs/0]).

%% type declarations
-type call()  :: {'load', atom()}
               | {'mfa_spec', mfa()}.
-type cast()  :: {'terminate', pid()}
               | {'return_spec', {mfa(), concolic_spec_parse:maybe_prefixed_type_sig()}, pid()}.
-type clogs() :: [atom()].
-type reply() :: {ok, ets:tab()}
               | concolic_load:compile_error()
               | 'preloaded'
               | 'cover_compiled'
               | 'non_existing'
               | concolic_spec_parse:maybe_prefixed_type_sig().
%% gen_server state datatype
-record(state, {
  %%-- Modules' database -------------------
  %% Creates an ETS table where it stores:
  %% {Key, Value} -> {Module, ModuleDb}
  %%   Module   :: atom()
  %%   ModuleDb :: ets:tab()
  db :: ets:tab(),          %% Database of the modules and their stored code
  dir :: string(),          %% Directory where .core files are saved
  waiting = orddict:new() :: [{{mfa_spec, mfa()}, pid()}], %% Info on the waiting processes
  workers = [] :: [pid()],  %% PIDs of the workers
  super :: pid()            %% Concolic Server (supervisor) process
}).
-type state() :: #state{}.

%% ============================================================================
%% External exports
%% ============================================================================

%% Initialize a CodeServer
-spec init_codeserver(string(), pid()) -> pid() | no_return().

init_codeserver(CoreDir, Super) ->
  case gen_server:start(?MODULE, [CoreDir, Super], []) of
    {ok, CodeServer} -> CodeServer;
    {error, Reason}  -> exit({codeserver_init, Reason})
  end.
  
%% Terminate a CodeServer
-spec terminate(pid()) -> ok.

terminate(CodeServer) ->
  gen_server:cast(CodeServer, {terminate, self()}).
  
%% Request the ETS table where the code of a module M is stored
-spec load(pid(), atom()) -> reply().

load(CodeServer, M) ->
  gen_server:call(CodeServer, {load, M}).

%% Request the spec of an MFA
-spec mfa_spec(pid(), mfa()) -> concolic_spec_parse:maybe_prefixed_type_sig().

mfa_spec(CodeServer, MFA) ->
  gen_server:call(CodeServer, {mfa_spec, MFA}, 10000).

%% Return the extracted spec of an MFA (Used by worker processes)
-spec return_spec(pid(), mfa(), concolic_spec_parse:maybe_prefixed_type_sig()) -> ok.

return_spec(CodeServer, MFA, MaybeSpec) ->
  gen_server:cast(CodeServer, {return_spec, {MFA, MaybeSpec}, self()}).

%% Get the attributes of a module
-spec module_attributes(pid(), module()) -> {ok, [{cerl:cerl(), cerl:cerl()}]} | error.

module_attributes(CodeServer, M) ->
  case load(CodeServer, M) of
   {ok, MDb} ->
    [{attributes, Attrs}] = ets:lookup(MDb, attributes),
    {ok, Attrs};
   _ ->
    error
  end.

%% Extract the spec of an MFA (Worker Process)
-spec spec_finder(pid(), mfa()) -> ok.

spec_finder(CodeServer, {M, _F, _A}=MFA) ->
  case load(CodeServer, M) of
    {ok, MDb} ->
      [{attributes, Attrs}] = ets:lookup(MDb, attributes),
      try
        MaybeSpec = concolic_spec_parse:retrieve_spec(CodeServer, MFA, Attrs),
        ok = return_spec(CodeServer, MFA, MaybeSpec)
      catch
        _:_ ->
          io:format("*** WARNING! Error parsing MFA spec! ***~n"),
          ok = return_spec(CodeServer, MFA, error)
      end;
    _ ->
      ok = return_spec(CodeServer, MFA, error)
  end.

%% ============================================================================
%% gen_server callbacks
%% ============================================================================

%% ------------------------------------------------------------------
%% gen_server callback : init/1
%% ------------------------------------------------------------------
-spec init([string() | pid(), ...]) -> {ok, state()}.

init([Dir, Super]) when is_list(Dir) ->
  link(Super),
  Db = ets:new(?MODULE, [ordered_set, protected]),
  U = erlang:ref_to_list(erlang:make_ref()) -- "#Ref<>",
  CoreDir = filename:absname(Dir ++ "/core-" ++ U),
  {ok, #state{db=Db, dir=CoreDir, super=Super}}.

%% ------------------------------------------------------------------
%% gen_server callback : terminate/2
%% ------------------------------------------------------------------
-spec terminate(term(), state()) -> 'ok'.

terminate(_Reason, State) ->
  Db = State#state.db,
  Dir = State#state.dir,
  Super = State#state.super,
  %% Delete all created ETS tables
  LoadedMods = delete_stored_modules(Db),
  ets:delete(Db),
  %% Clean up .core files dir
  delete_stored_core_files(Dir),
  %% Send statistics to supervisor
  ok = concolic:send_clogs(Super, LoadedMods).
  
%% ------------------------------------------------------------------
%% gen_server callback : code_change/3
%% ------------------------------------------------------------------
-spec code_change(term(), state(), term()) -> {ok, state()}.
  
code_change(_OldVsn, State, _Extra) ->
  {ok, State}.  %% No change planned.
  
%% ------------------------------------------------------------------
%% gen_server callback : handle_info/2
%% ------------------------------------------------------------------
-spec handle_info(term(), state()) -> {'noreply', state()}.
  
handle_info(Msg, State) ->
  %% Just outputting unexpected messages for now
  io:format("[~s]: Unexpected message ~p~n", [?MODULE, Msg]),
  {noreply, State}.
  
%% ------------------------------------------------------------------
%% gen_server callback : handle_call/3
%% ------------------------------------------------------------------
-spec handle_call(call(), {pid(), reference()}, state()) -> {reply, reply(), state()}.
  
%% Handle a "Load a Module into the Db" call
%%   Case                             Reply
%% --------                         ---------
%% Module M is already loaded  -->  {ok, MDb}
%% Module M is not loaded yet  -->  false
%% Module M is preloaded       -->  preloaded
%% Module M is cover_compiled  -->  cover_compiled
%% Module M does not exist     -->  non_existing
handle_call({load, M}, _From, State) ->
  %%  io:format("[load]: Got request for module : ~p~n", [M]),
  case is_mod_stored(M, State) of
    {true, MDb} ->
      {reply, {ok, MDb}, State};
    false ->
      %% Load module M
      Reply = load_mod(M, State),
      {reply, Reply, State};
    preloaded ->
      {reply, preloaded, State};
    cover_compiled ->
      {reply, cover_compiled, State};
    non_existing ->
      {reply, non_existing, State}
  end;

handle_call({mfa_spec, MFA}, From, S=#state{waiting = Wa, workers = Ws}) ->
  Worker = spawn(?MODULE, spec_finder, [self(), MFA]),
  {noreply, S#state{
    waiting = orddict:store({mfa_spec, MFA}, From, Wa),
    workers = [Worker | Ws]
  }}.

%% ------------------------------------------------------------------
%% gen_server callback : handle_cast/2
%% ------------------------------------------------------------------
-spec handle_cast(cast(), state()) -> {stop, normal, state()}
                                    | {noreply, state()}.

handle_cast({terminate, FromWho}, State) ->
  Super = State#state.super,
  case FromWho =:= Super of
    true  -> {stop, normal, State};
    false -> {noreply, State}
  end;

handle_cast({return_spec, {MFA, MaybeSpec}, Worker}, S=#state{waiting = Wa, workers = Ws}) ->
  Key = {mfa_spec, MFA},
  Who = orddict:fetch(Key, Wa),
  gen_server:reply(Who, MaybeSpec),
  {noreply, S#state{
    waiting = orddict:erase(Key, Wa),
    workers = lists:delete(Worker, Ws)
  }}.


%% ============================================================================
%% Internal functions
%% ============================================================================

%% Load a module's code
load_mod(M, #state{db = Db, dir = Dir}) ->
  %% Create an ETS table to store the code of the module
  MDb = ets:new(M, [ordered_set, protected]),
  ets:insert(Db, {M, MDb}),
  
  %% Load the code of the module
  Reply = concolic_load:load(M, MDb, Dir),
  case Reply of
    {ok, M} ->
%      io:format("[load (~w)]: Loaded module ~p~n", [node(), M]),
      {ok, MDb};
    _ -> Reply
  end.

%% Check if a Module is stored in the Db
%%   Case                             Reply
%% --------                         ---------
%% Module M is already loaded  -->  {true, MDb}
%% Module M is not loaded yet  -->  false
%% Module M is preloaded       -->  preloaded
%% Module M is cover_compiled  -->  cover_compiled
%% Module M does not exist     -->  non_existing
-spec is_mod_stored(atom(), state()) -> {'true', ets:tab()}
                                      | 'false' 
                                      | 'preloaded' 
                                      | 'cover_compiled' 
                                      | 'non_existing'.
  
is_mod_stored(M, State) ->
  Db = State#state.db,
  case ets:lookup(Db, M) of
    [{M, MDb}] ->
      {true, MDb};
    [] ->
      case code:which(M) of
        non_existing   -> non_existing;
        preloaded      -> preloaded;
        cover_compiled -> cover_compiled;
        _Path          -> false
      end
  end.

%% Delete all ETS tables that contain the code of modules
-spec delete_stored_modules(ets:tab()) -> clogs().
  
delete_stored_modules(Db) ->
  DeleteOne = 
    fun ({Mod, ModDb}, Acc) ->
      ets:delete(ModDb),
      [Mod | Acc]
    end,
  ets:foldl(DeleteOne, [], Db).
  
%% Delete all the created .core files during the execution
-spec delete_stored_core_files(file:name()) -> 'ok'.
  
delete_stored_core_files(Dir) ->
  case file:list_dir(Dir) of
    {ok, FileNames} ->
      lists:foreach(fun(FN) -> file:delete(Dir ++ "/" ++ FN) end, FileNames),
      ok = file:del_dir(Dir);
    {error, enoent} ->
      ok
  end.
  
