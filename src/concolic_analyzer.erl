%% -*- erlang-indent-level: 2 -*-
%%------------------------------------------------------------------------------
-module(concolic_analyzer).

-export([get_execution_vertices/1,
         get_traces/1, get_result/1, get_mapping/1,
         clear_and_delete_dir/1, print_trace/1]).

%% exported types
-export_type([path_vertex/0, traces/0, internal_error/0, result/0, ret/0]).

-include("concolic_internal.hrl").
-include("concolic_flags.hrl").

-type traces() :: [{node(), [file:name()]}].
-type path_vertex() :: [?CONSTRAINT_TRUE_REP | ?CONSTRAINT_FALSE_REP]. %% [$T | $F]
-type internal_error() :: 'internal_concolic_error'
                        | 'internal_codeserver_error'
                        | 'internal_traceserver_error'.
-type result() :: {'ok', node(), concolic:exec_info()}
                | {'runtime_error', node(), concolic:exec_info()}
                | {internal_error(), term()}.
-type ret()    :: {'ok', concolic_eval:result()}         %% Successful Execution
                | {'error', term()}                      %% Runtime Error
                | {'internal_error', internal_error()}.  %% Internal Error

%%====================================================================
%% External exports
%%====================================================================

%% ------------------------------------------------------------------
%% Extract Information from the Concolic Result
%% (as returned from the Concolic Server)
%% ------------------------------------------------------------------

%% Retrieve the outcome of the concolic execution
%% from the resulting execution information
-spec get_result(result()) -> ret().

get_result({'ok', Node, R}) ->
  {ok, Info} = orddict:find(Node, R),
  {ok, proplists:get_value('result', Info)};
get_result({'runtime_error', Node, R}) ->
  {ok, Info} = orddict:find(Node, R),
  {Node, _Who, {CErr, _Serr}} = proplists:get_value('runtime_error', Info),
  {'error', CErr};
get_result({Error, _Node, _R}) ->
  {'internal_error', Error}.

%% Create a proplist with the trace files in the form:
%% [{Node, Files}] where Node :: node(), Files :: [file:name()]
-spec get_traces(result()) -> traces().

get_traces({_Status, _Node, Results}) ->
  Ns = orddict:to_list(Results),
  [{N, get_traces_files(R)} || {N, R} <- Ns].

get_traces_files(R) ->
  Logs = proplists:get_value('tlogs', R),
  Dir = proplists:get_value('dir', Logs),
  {ok, Fs} = file:list_dir(Dir),
  [Dir ++ "/" ++ F || F <- Fs].

%% Retrieve the mapping of the concrete to symbolic values
-spec get_mapping(result()) -> [concolic_symbolic:mapping()].

get_mapping({X, _Node, Result}) when X =:= 'ok'; X =:= 'runtime_error' ->
  {ok, Info} = orddict:find(node(), Result),
  proplists:get_value('mapping', Info);
get_mapping({_Error, _Node, _R}) -> [].

%% ------------------------------------------------------------------
%% Operations on execution traces
%% ------------------------------------------------------------------

%% Extract Path Vertices from the execution traces
-spec get_execution_vertices(traces()) -> [{node(), [path_vertex()]}].

get_execution_vertices(Paths) ->
  get_execution_vertices(Paths, []).

get_execution_vertices([], Acc) ->
  lists:reverse(Acc);
get_execution_vertices([{Node, Fs} | Rest], Acc) ->
  Vs = [concolic_encdec:path_vertex(F) || F <- Fs],
  get_execution_vertices(Rest, [{Node, Vs} | Acc]).

%% Print the contents of a trace file
-spec print_trace(file:name()) -> ok.

print_trace(D) ->
  {ok, F} = concolic_encdec:open_file(D, 'read'),
  concolic_encdec:pprint(F),
  concolic_encdec:close_file(F).

%% Delete the trace files / folders of an execution
-spec clear_and_delete_dir(string()) -> ok.

clear_and_delete_dir(D) ->
  {ok, CWD} = file:get_cwd(),
  clear_dir(CWD ++ "/" ++ D).

clear_dir(D) ->
  case filelib:is_regular(D) of
    true ->
      delete_file(D);
    false ->
      case file:del_dir(D) of
        ok -> ok;
        {error, eexist} ->
          {ok, L} = file:list_dir(D),
          LL = [D ++ "/" ++ X || X <- L],
          lists:foreach(fun clear_dir/1, LL),
          file:del_dir(D);
        _ -> ok
      end
  end.

-ifdef(DELETE_TRACE).
delete_file(F) -> file:delete(F).
-else.
delete_file(_F) -> ok.
-endif.

