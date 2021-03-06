import json
from z3 import *

class Env:
  def __init__(self):
    self.cnt = 0
    self.e = {}
    self.params = []
  
  def add_param(self, x):
    self.params.append(x)
  
  def lookup(self, x):
    if (x in self.e):
      return self.e[x]
    else:
      return None
  
  def fresh_var(self, s, Type):
    self.cnt += 1
    x =  Const("x%s" % self.cnt, Type)
    self.e[s] = x
    return x

class ErlangZ3:
  def __init__(self):
    self.Term, self.List, self.Atom = self.erlang_types()
    self.env = Env()
    self.solver = Solver()
    self.atom_true = self.json_term_to_z3(json.loads("{\"t\" : \"Atom\", \"v\" : [116,114,117,101]}"))
    self.atom_false = self.json_term_to_z3(json.loads("{\"t\" : \"Atom\", \"v\" : [102,97,108,115,101]}"))
    self.atom_infinity = self.json_term_to_z3(json.loads("{\"t\" : \"Atom\", \"v\" : [105,110,102,105,110,105,116,121]}"))
    self.max_len = 100
    self.check = None
    self.model = None
  
  ## Solve a Constraint Set
  def solve(self):
    self.check = self.solver.check()
    if (self.check == sat):
      self.model = self.solver.model()
      return True
    else:
      return False
  
  ## Define the Erlang Type System
  def erlang_types(*args):
    Term = Datatype('Term')
    List = Datatype('List')
    Tuple = Datatype('Tuple')
    Atom = Datatype('Atom')
    # Term
    Term.declare('int', ('ival', IntSort()))
    Term.declare('real', ('rval', RealSort()))
    Term.declare('lst', ('lval', List))
    Term.declare('tpl', ('tval', List))
    Term.declare('atm', ('aval', Atom))
    # List
    List.declare('nil')
    List.declare('cons', ('hd', Term), ('tl', List))
    # Atom
    Atom.declare('anil')
    Atom.declare('acons', ('ahd', IntSort()), ('atl', Atom))
    # Return Datatypes
    return CreateDatatypes(Term, List, Atom)
  
  ## Encode an Erlang term in JSON representation to Z3
  def json_term_to_z3(self, json_data):
    if ("s" in json_data):
      return self._json_symbolic_term_to_z3(json_data)
    else:
      d = json_data["d"] if ("d" in json_data) else {}
      return self._json_concrete_term_to_z3(json_data, d)
  
  def _json_symbolic_term_to_z3(self, json_data):
    e = self.env
    s = json_data["s"]
    v = e.lookup(s)
    if (v is not None):
      return v
    else:
      x = e.fresh_var(s, self.Term)
      return x
  
  def _json_concrete_term_to_z3(self, json_data, d):
    e = self.env
    if ("l" in json_data):
      return self._json_alias_term_to_z3(json_data, d)
    else:
      opts = {
        "Int" : self._json_int_term_to_z3,
        "Real" : self._json_real_term_to_z3,
        "List" : self._json_list_term_to_z3,
        "Tuple" : self._json_tuple_term_to_z3,
        "Atom" : self._json_atom_term_to_z3,
      }
      return opts[json_data["t"]](json_data["v"], d)
  
  
  def _json_int_term_to_z3(self, val, d):
    return self.Term.int(val)
  
  def _json_real_term_to_z3(self, val, d):
    return self.Term.real(val)
  
  def _json_list_term_to_z3(self, val, d):
    val.reverse()
    term = self.List.nil
    while val != []:
      head, val = val[0], val[1:]
      enc_head = self._json_concrete_term_to_z3(head, d)
      term = self.List.cons(enc_head, term)
    return self.Term.lst(term)
  
  def _json_tuple_term_to_z3(self, val, d):
    val.reverse()
    term = self.List.nil
    while val != []:
      head, val = val[0], val[1:]
      enc_head = self._json_concrete_term_to_z3(head, d)
      term = self.List.cons(enc_head, term)
    return self.Term.tpl(term)
  
  def _json_atom_term_to_z3(self, val, d):
    val.reverse()
    term = self.Atom.anil
    while val != []:
      head, val = val[0], val[1:]
      term = self.Atom.acons(head, term)
    term = self.Term.atm(term)
    return term
  
  def _json_alias_term_to_z3(self, json_data, d):
    e = self.env
    s = json_data["l"]
    v = e.lookup(s)
    if (v is not None):
      return v
    else:
      x = e.fresh_var(s, self.Term)
      y = self._json_concrete_term_to_z3(d[s], d)
      self.solver.add(x == y)
      return x
  
  ## Decode a Z3 object to an Erlang term in JSON representation
  def z3_term_to_json(self, term):
    T = self.Term
    if (is_true(simplify(T.is_int(term)))):
      return self._z3_int_term_to_json(T.ival(term))
    elif (is_true(simplify(T.is_real(term)))):
      return self._z3_real_term_to_json(T.rval(term))
    elif (is_true(simplify(T.is_lst(term)))):
      return self._z3_list_term_to_json(T.lval(term))
    elif (is_true(simplify(T.is_tpl(term)))):
      return self._z3_tuple_term_to_json(T.tval(term))
    elif (is_true(simplify(T.is_atm(term)))):
      return self._z3_atom_term_to_json(T.aval(term))
  
  def _z3_int_term_to_json(self, t):
    return {"t" : "Int", "v" : simplify(t).as_long()}
  
  def _z3_real_term_to_json(self, t):
    s = simplify(t)
    f = float(s.numerator_as_long()) / float(s.denominator_as_long())
    return {"t" : "Real", "v" : f}
  
  def _z3_list_term_to_json(self, t):
    L = self.List
    s = simplify(t)
    r = []
    while (is_true(simplify(L.is_cons(s)))):
      hd = simplify(L.hd(s))
      s = simplify(L.tl(s))
      r.append(self.z3_term_to_json(hd))
    return {"t" : "List", "v" : r}
  
  def _z3_tuple_term_to_json(self, t):
    L = self.List
    s = simplify(t)
    r = []
    while (is_true(simplify(L.is_cons(s)))):
      hd = simplify(L.hd(s))
      s = simplify(L.tl(s))
      r.append(self.z3_term_to_json(hd))
    return {"t" : "Tuple", "v" : r}
  
  def _z3_atom_term_to_json(self, t):
    A = self.Atom
    s = simplify(t)
    r = []
    while (is_true(simplify(A.is_acons(s)))):
      hd = simplify(A.ahd(s))
      s = simplify(A.atl(s))
      r.append(simplify(hd).as_long())
    return {"t" : "Atom", "v" : r}
  
  ## Encode Commands in JSON representation to Z3
  def json_command_to_z3(self, json_data):
    opts = {
      # Constraint Commands
      "Eq" : self._json_cmd_eq_to_z3,
      "Neq" : self._json_cmd_neq_to_z3,
      "T" : self._json_cmd_true_to_z3,
      "F" : self._json_cmd_false_to_z3,
      "Nel" : self._json_cmd_nel_to_z3,
      "El" : self._json_cmd_el_to_z3,
      "Nl" : self._json_cmd_nl_to_z3,
      "Nt" : self._json_cmd_nt_to_z3,
      "Ts" : self._json_cmd_ts_to_z3,
      "Nts" : self._json_cmd_nts_to_z3,
      # Operator Commands
      "=:=" : self._json_bif_seq_to_z3,
      "=/=" : self._json_bif_sneq_to_z3,
      "+" : self._json_bif_add_to_z3,
      "-" : self._json_bif_minus_to_z3,
      "*" : self._json_bif_mult_to_z3,
      "/" : self._json_bif_rdiv_to_z3,
      "div" : self._json_bif_div_to_z3,
      "rem" : self._json_bif_rem_to_z3,
      "or" : self._json_bif_or_to_z3,
      "and" : self._json_bif_and_to_z3,
      "ore" : self._json_bif_orelse_to_z3,
      "anda" : self._json_bif_andalso_to_z3,
      "not" : self._json_bif_not_to_z3,
      "xor" : self._json_bif_xor_to_z3,
      "<" : self._json_bif_lt_to_z3,
      ">" : self._json_bif_gt_to_z3,
      ">=" : self._json_bif_gteq_to_z3,
      "=<" : self._json_bif_lteq_to_z3,
      # BIF Commands
      "hd" : self._json_bif_hd_to_z3,
      "tl" : self._json_bif_tl_to_z3,
      "abs" : self._json_bif_abs_to_z3,
      "elm" : self._json_bif_elem_to_z3,
      "flt" : self._json_bif_float_to_z3,
      "isa" : self._json_bif_is_atom_to_z3,
      "isb" : self._json_bif_is_boolean_to_z3,
      "isf" : self._json_bif_is_float_to_z3,
      "isi" : self._json_bif_is_integer_to_z3,
      "isl" : self._json_bif_is_list_to_z3,
      "isn" : self._json_bif_is_number_to_z3,
      "ist" : self._json_bif_is_tuple_to_z3,
      "rnd" : self._json_bif_round_to_z3,
      "trc" : self._json_bif_trunc_to_z3,
      "ltt" : self._json_bif_list_to_tuple_to_z3,
      "ttl" : self._json_bif_tuple_to_list_to_z3,
      "len" : self._json_bif_length_to_z3,
      "tpls" : self._json_bif_tuple_size_to_z3,
      "mtpl2": self._json_bif_make_tuple_2_to_z3,
      # Other Useful Commands
      "Pms" : self._json_cmd_define_params_to_z3,
      "Psp" : self._json_cmd_parameter_spec_to_z3,
      "Bkt" : self._json_cmd_break_tuple_to_z3,
      "Bkl" : self._json_cmd_break_list_to_z3,
    }
    opts_rev = {
      # Reversed Constraint Commands
      "Eq" : self._json_cmd_neq_to_z3,
      "Neq" : self._json_cmd_eq_to_z3,
      "T" : self._json_cmd_false_to_z3,
      "F" : self._json_cmd_true_to_z3,
      "Nel" : self._json_rev_cmd_nel_to_z3,
      "El" : self._json_cmd_nel_to_z3,
      "Nl" : self._json_cmd_nel_to_z3,
      "Ts" : self._json_rev_cmd_ts_to_z3,
      "Nt" : self._json_cmd_ts_to_z3,
      "Nts" : self._json_cmd_ts_to_z3,
    }
    if ("r" in json_data):
      opts_rev[json_data["c"]](*json_data["a"])
    else:
      opts[json_data["c"]](*json_data["a"])
  
  # Constraints
  
  # "Equal"
  def _json_cmd_eq_to_z3(self, term1, term2):
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    self.solver.add(t1 == t2)
  
  # "Not Equal"
  def _json_cmd_neq_to_z3(self, term1, term2):
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    self.solver.add(t1 != t2)
  
  # "Guard True"
  def _json_cmd_true_to_z3(self, term):
    t = self.json_term_to_z3(term)
    self.solver.add(t == self.atom_true)
  
  # "Guard False"
  def _json_cmd_false_to_z3(self, term):
    t = self.json_term_to_z3(term)
    self.solver.add(t == self.atom_false)
  
  # "Non Empty List"
  def _json_cmd_nel_to_z3(self, term):
    s = self.solver
    t = self.json_term_to_z3(term)
    s.add(self.Term.is_lst(t))
    s.add(self.List.is_cons(self.Term.lval(t)))
  
  # Reversed "Non Empty List"
  def _json_rev_cmd_nel_to_z3(self, term):
    t = self.json_term_to_z3(term)
    e = And(
      self.Term.is_lst(t),
      self.List.is_cons(self.Term.lval(t))
    )
    self.solver.add(Not(e))
  
  # "Empty List"
  def _json_cmd_el_to_z3(self, term):
    t = self.json_term_to_z3(term)
    s.add(self.Term.is_lst(t))
    s.add(self.List.is_nil(self.Term.lval(t)))
  
  # "Not List"
  def _json_cmd_nl_to_z3(self, term):
    t = self.json_term_to_z3(term)
    self.solver.add(self.Term.is_lst(t) == False)
  
  # "Tuple of size N"
  def _json_cmd_ts_to_z3(self, term, l_json):
    s = self.solver
    t = self.json_term_to_z3(term)
    l = l_json["v"] # Expect l_json to represent an Integer
    s.add(self.Term.is_tpl(t))
    t = self.Term.tval(t)
    for i in range(0, l):
      s.add(self.List.is_cons(t))
      t = self.List.tl(t)
    s.add(t == self.List.nil)
  
  # Reversed "Tuple of size N"
  def _json_rev_cmd_ts_to_z3(self, term, l_json):
    t = self.json_term_to_z3(term)
    l = l_json["v"] # Expect l_json to represent an Integer
    e = [self.Term.is_tpl(t)]
    t = self.Term.tval(t)
    for i in range(0, l):
      e.append(self.List.is_cons(t))
      t = self.List.tl(t)
    e.append(t == self.List.nil)
    self.solver.add(Not(And(*e)))
  
  # "Tuple Not of size N"
  def _json_cmd_nts_to_z3(self, term, l_json):
    s = self.solver
    t = self.json_term_to_z3(term)
    l = l_json["v"] # Expect l_json to represent an Integer
    s.add(self.Term.is_tpl(t))
    preds = []
    t = self.Term.tval(t)
    for i in range(0, l):
      preds.append(self.List.is_cons(t))
      t = self.List.tl(t)
    preds.append(t == self.List.nil)
    s.add(Not(And(*preds)))
  
  # "Not Tuple"
  def _json_cmd_nt_to_z3(self, term, l_json):
    t = self.json_term_to_z3(term)
    self.solver.add(self.Term.is_tpl(t) == False)
  
  # Other Useful Commands
  
  # Define Parameters
  def _json_cmd_define_params_to_z3(self, *args):
    for s in args:
      x = self._json_symbolic_term_to_z3(s)
      self.env.add_param(s["s"])
  
  # Parameter Spec
  def _json_cmd_parameter_spec_to_z3(self, term, typesig):
    t = self.json_term_to_z3(term)
    Ax = self._bind_term_to_typesig(t, typesig)
#    print "Ax", Ax
    if Ax != None:
      self.solver.add(Ax)
  
  # Bind a variable to a specific type
  def _bind_term_to_typesig(self, x, typesig):
    opts = {
      "literal" : self._bind_term_to_literal,
      "any" : self._bind_term_to_any,
      "atom" : self._bind_term_to_atom,
      "boolean" : self._bind_term_to_boolean,
      "byte" : self._bind_term_to_byte,
      "char" : self._bind_term_to_char,
      "float" : self._bind_term_to_float,
      "integer" : self._bind_term_to_integer,
      "list" : self._bind_term_to_list,
      "nelist" : self._bind_term_to_nelist,
      "number" : self._bind_term_to_number,
      "range" : self._bind_term_to_range,
      "string" : self._bind_term_to_string,
      "nestring" : self._bind_term_to_nestring,
      "timeout" : self._bind_term_to_timeout,
      "tuple" : self._bind_term_to_tuple,
      "union" : self._bind_term_to_union,
    }
    info = typesig["i"] if "i" in typesig else None
    args = typesig["a"] if "a" in typesig else []
    return opts[typesig["t"]](x, info, args)
  
  # Bind variable to literal
  def _bind_term_to_literal(self, x, info, args):
    l = self.json_term_to_z3(info)
    return x == l
  
  # Bind variable to any()
  def _bind_term_to_any(self, x, info, args):
    return None
  
  # Bind variable to atom()
  def _bind_term_to_atom(self, x, info, args):
    return self.Term.is_atm(x)
  
  # Bind variable to boolean()
  def _bind_term_to_boolean(self, x, info, args):
    return Or(x == self.atom_true, x == self.atom_false)
  
  # Bind variable to byte()
  def _bind_term_to_byte(self, x, info, args):
    Ax = [self.Term.is_int(x)]
    Ax.append(self.Term.ival(x) >= 0)
    Ax.append(self.Term.ival(x) <= 255)
    return And(*Ax)
  
  # Bind variable to char()
  def _bind_term_to_char(self, x, info, args):
    Ax = []
    Ax.append(self.Term.is_int(x))
    Ax.append(self.Term.ival(x) >= 0)
    Ax.append(self.Term.ival(x) <= 1114111) # 16#10ffff
    return And(*Ax)
  
  # Bind variable to float()
  def _bind_term_to_float(self, x, info, args):
    return self.Term.is_real(x)
  
  # Bind variable to integer types
  def _bind_term_to_integer(self, x, info, args):
    Ax = [self.Term.is_int(x)]
    opts = {
      "pos" : self.Term.ival(x) > 0,
      "neg" : self.Term.ival(x) < 0,
      "non_neg" : self.Term.ival(x) >= 0,
    }
    if info != "any":
      Ax.append(opts[info])
    return And(*Ax)
  
  # Bind variable to list()
  def _bind_term_to_list(self, x, info, args):
    return self._bind_term_to_list_h(x, info, False, False)
  
  # Bind variable to non empty list()
  def _bind_term_to_nelist(self, x, info, args):
    return self._bind_term_to_list_h(x, info, True, False)
  
  # Bind variable to string()
  def _bind_term_to_string(self, x, info, args):
    return self._bind_term_to_list_h(x, info, False, True)
  
  # Bind variable to non empty string()
  def _bind_term_to_nestring(self, x, info, args):
    return self._bind_term_to_list_h(x, info, True, True)
  
  # Helper function for binding variables to lists and strings
  def _bind_term_to_list_h(self, x, info, NonEmpty, String):
    T = self.Term
    L = self.List
    es = [T.is_lst(x)]
    x = T.lval(x)
    if NonEmpty:
      es.append(L.is_cons(x))
    if String or info["t"] != "any":
      acc = []
      for i in range (0, self.max_len):
        h = L.hd(x)
        if String:
          typesig = self._bind_term_to_char(h, "", "")
        else:
          typesig = self._bind_term_to_typesig(h, info)
        acc.append(
          (L.is_cons(x), typesig, L.is_nil(x))
        )
        x = L.tl(x)
      ax = None
      for (c, t, f) in reversed(acc):
        if ax == None:
          ax = If(c, t, f)
        else:
          ax = If(c, And(t, ax), f)
      es.append(ax)
    return And(*es)
  
  # Bind variable to number()
  def _bind_term_to_number(self, x, info, args):
    return Or(self.Term.is_int(x), self.Term.is_real(x))
  
  # Bind variable to range()
  def _bind_term_to_range(self, x, info, args):
    # Assume that from / to are integer literals
    T = self.Term
    Ax = [T.is_int(x)]
    from_r = self.json_term_to_z3(args[0]["i"])
    to_r = self.json_term_to_z3(args[1]["i"])
    Ax.append(T.ival(x) >= T.ival(from_r))
    Ax.append(T.ival(x) <= T.ival(to_r))
    return simplify(And(*Ax))
  
  # Bind variable to timeout()
  def _bind_term_to_timeout(self, x, info, args):
    T = self.Term
    return Or(
      x == self.atom_infinity,
      And(T.is_int(x), T.ival(x) >= 0)
    )
    
  # Bind variable to tuple()
  def _bind_term_to_tuple(self, x, info, args):
    T = self.Term
    L = self.List
    Ax = [T.is_tpl(x)]
    if args == []:
      return Ax[0]
    else:
      x = T.tval(x)
      for typesig in args:
        Ax.append(L.is_cons(x))
        e = self._bind_term_to_typesig(L.hd(x), typesig)
        if e != None:
          Ax.append(e)
        x = L.tl(x)
      Ax.append(L.is_nil(x))
      return And(*Ax)
  
  # Bind variable to union()
  def _bind_term_to_union(self, x, info, args):
    Ax = []
    for typesig in args:
      Ax.append(self._bind_term_to_typesig(x, typesig))
    return Or(*Ax)
  
  # 'Break Tuple'
  def _json_cmd_break_tuple_to_z3(self, term1, terms):
    s = self.solver
    t1 = self.json_term_to_z3(term1)
    s.add(self.Term.is_tpl(t1))
    t1 = self.Term.tval(t1)
    for term in terms:
      t = self.json_term_to_z3(term)
      s.add(self.List.is_cons(t1))
      s.add(t == self.List.hd(t1))
      t1 = self.List.tl(t1)
    s.add(t1 == self.List.nil)
  
  # 'Break List'
  def _json_cmd_break_list_to_z3(self, term1, terms):
    s = self.solver
    t1 = self.json_term_to_z3(term1)
    s.add(self.Term.is_lst(t1))
    t1 = self.Term.lval(t1)
    for term in terms:
      t = self.json_term_to_z3(term)
      s.add(self.List.is_cons(t1))
      s.add(t == self.List.hd(t1))
      t1 = self.List.tl(t1)
    s.add(t1 == self.List.nil)
  
  # BIFs
  
  # erlang:hd/1
  def _json_bif_hd_to_z3(self, term1, term2):
    s = self.solver
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    s.add(self.Term.is_lst(t1))
    s.add(self.List.is_cons(self.Term.lval(t1)))
    s.add(self.List.hd(self.Term.lval(t1)) == t2)
  
  # erlang:tl/1
  def _json_bif_tl_to_z3(self, term1, term2):
    s = self.solver
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    s.add(self.Term.is_lst(t1))
    s.add(self.List.is_cons(self.Term.lval(t1)))
    s.add(self.Term.lst(self.List.tl(self.Term.lval(t1))) == t2)
  
  # erlang:'=:='/2
  def _json_bif_seq_to_z3(self, term1, term2, term3):
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    self.solver.add(If(t1 == t2, t3 == self.atom_true, t3 == self.atom_false))
  
  # erlang:'=/='/2
  def _json_bif_sneq_to_z3(self, term1, term2, term3):
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    self.solver.add(If(t1 != t2, t3 == self.atom_true, t3 == self.atom_false))
  
  # erlang:'+'/2
  def _json_bif_add_to_z3(self, term1, term2, term3):
    T = self.Term
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    self.solver.add(Or(
      And(T.is_int(t1), T.is_int(t2), T.is_int(t3), T.ival(t1) + T.ival(t2) == T.ival(t3)),
      And(T.is_int(t1), T.is_real(t2), T.is_real(t3), T.ival(t1) + T.rval(t2) == T.rval(t3)),
      And(T.is_real(t1), T.is_int(t2), T.is_real(t3), T.rval(t1) + T.ival(t2) == T.rval(t3)),
      And(T.is_real(t1), T.is_real(t2), T.is_real(t3), T.rval(t1) + T.rval(t2) == T.rval(t3))
    ))
  
  # erlang:'-'/2
  def _json_bif_minus_to_z3(self, term1, term2, term3):
    T = self.Term
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    self.solver.add(Or(
      And(T.is_int(t1), T.is_int(t2), T.is_int(t3), T.ival(t1) - T.ival(t2) == T.ival(t3)),
      And(T.is_int(t1), T.is_real(t2), T.is_real(t3), T.ival(t1) - T.rval(t2) == T.rval(t3)),
      And(T.is_real(t1), T.is_int(t2), T.is_real(t3), T.rval(t1) - T.ival(t2) == T.rval(t3)),
      And(T.is_real(t1), T.is_real(t2), T.is_real(t3), T.rval(t1) - T.rval(t2) == T.rval(t3))
    ))
  
  # erlang:'*'/2
  def _json_bif_mult_to_z3(self, term1, term2, term3):
    T = self.Term
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    self.solver.add(Or(
      And(T.is_int(t1), T.is_int(t2), T.is_int(t3), T.ival(t1) * T.ival(t2) == T.ival(t3)),
      And(T.is_int(t1), T.is_real(t2), T.is_real(t3), T.ival(t1) * T.rval(t2) == T.rval(t3)),
      And(T.is_real(t1), T.is_int(t2), T.is_real(t3), T.rval(t1) * T.ival(t2) == T.rval(t3)),
      And(T.is_real(t1), T.is_real(t2), T.is_real(t3), T.rval(t1) * T.rval(t2) == T.rval(t3))
    ))
  
  # erlang:'/'/2
  def _json_bif_rdiv_to_z3(self, term1, term2, term3):
    T = self.Term
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    self.solver.add(T.is_real(t3))
    self.solver.add(Or(
      And(T.is_int(t1), T.is_int(t2), T.ival(t2) != 0, T.ival(t1) / ToReal(T.ival(t2)) == T.rval(t3)),
      And(T.is_int(t1), T.is_real(t2), T.rval(t2) != 0.0, T.ival(t1) / T.rval(t2) == T.rval(t3)),
      And(T.is_real(t1), T.is_int(t2), T.ival(t2) != 0, T.rval(t1) / T.ival(t2) == T.rval(t3)),
      And(T.is_real(t1), T.is_real(t2), T.rval(t2) != 0.0, T.rval(t1) / T.rval(t2) == T.rval(t3))
    ))
  
  # erlang:'div'/2
  def _json_bif_div_to_z3(self, term1, term2, term3):
    s = self.solver
    T = self.Term
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    s.add(T.is_int(t1))
    s.add(T.is_int(t2))
    s.add(T.is_int(t3))
    s.add(T.ival(t2) != 0)
    s.add(T.ival(t1) / T.ival(t2) == T.ival(t3))
  
  # erlang:'rem'/2
  def _json_bif_rem_to_z3(self, term1, term2, term3):
    s = self.solver
    T = self.Term
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    s.add(T.is_int(t1))
    s.add(T.is_int(t2))
    s.add(T.is_int(t3))
    s.add(T.ival(t2) != 0)
    s.add(T.ival(t1) % T.ival(t2) == T.ival(t3))
  
  # erlang:abs/1
  def _json_bif_abs_to_z3(self, term1, term2):
    T = self.Term
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    iabs = Or(
      And(T.ival(t1) < 0, T.ival(t2) == -T.ival(t1)),
      And(T.ival(t1) >= 0, T.ival(t2) == T.ival(t1)),
    )
    rabs = Or(
      And(T.rval(t1) < 0.0, T.rval(t2) == -T.rval(t1)),
      And(T.rval(t1) >= 0.0, T.rval(t2) == T.rval(t1)),
    )
    self.solver.add(Or(
      And(T.is_int(t1), T.is_int(t2), iabs),
      And(T.is_real(t1), T.is_real(t2), rabs),
    ))
  
  # erlang:'or'/2
  def _json_bif_or_to_z3(self, term1, term2, term3):
    T = self.atom_true
    F = self.atom_false
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    self.solver.add(Or(
      And(t1 == T, t2 == F, t3 == T),
      And(t1 == F, t2 == T, t3 == T),
      And(t1 == T, t2 == T, t3 == T),
      And(t1 == F, t2 == F, t3 == F)
    ))
  
  # erlang:'and'/2
  def _json_bif_and_to_z3(self, term1, term2, term3):
    T = self.atom_true
    F = self.atom_false
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    self.solver.add(Or(
      And(t1 == T, t2 == F, t3 == F),
      And(t1 == F, t2 == T, t3 == F),
      And(t1 == T, t2 == T, t3 == T),
      And(t1 == F, t2 == F, t3 == F)
    ))
  
  # erlang:'orelse'/2
  def _json_bif_orelse_to_z3(self, term1, term2, term3):
    T = self.atom_true
    F = self.atom_false
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    self.solver.add(Or(
      And(t1 == T, t3 == T),
      And(t1 == F, t2 == T, t3 == T),
      And(t1 == F, t2 == F, t3 == F)
    ))
  
  # erlang:'andalso'/2
  def _json_bif_andalso_to_z3(self, term1, term2, term3):
    T = self.atom_true
    F = self.atom_false
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    self.solver.add(Or(
      And(t1 == T, t2 == F, t3 == F),
      And(t1 == T, t2 == T, t3 == T),
      And(t1 == F, t3 == F)
    ))
  
  # erlang:'not'/2
  def _json_bif_not_to_z3(self, term1, term2):
    T = self.atom_true
    F = self.atom_false
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    self.solver.add(Or(
      And(t1 == T, t2 == F),
      And(t1 == F, t2 == T)
    ))
  
  # erlang:'xor'/2
  def _json_bif_xor_to_z3(self, term1, term2, term3):
    T = self.atom_true
    F = self.atom_false
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    self.solver.add(Or(
      And(t1 == T, t2 == F, t3 == T),
      And(t1 == F, t2 == T, t3 == T),
      And(t1 == T, t2 == T, t3 == F),
      And(t1 == F, t2 == F, t3 == F)
    ))
  
  # erlang:'<'/2
  #!# SIMPLIFIED (Only supports numbers)
  def _json_bif_lt_to_z3(self, term1, term2, term3):
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    ax = self._term_comparison("<", t1, t2, t3)
    self.solver.add(ax)
  
  # erlang:'>'/2
  #!# SIMPLIFIED (Only supports numbers)
  def _json_bif_gt_to_z3(self, term1, term2, term3):
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    ax = self._term_comparison(">", t1, t2, t3)
    self.solver.add(ax)
  
  # erlang:'>='/2
  #!# SIMPLIFIED (Only supports numbers)
  def _json_bif_gteq_to_z3(self, term1, term2, term3):
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    ax = self._term_comparison(">=", t1, t2, t3)
    self.solver.add(ax)
  
  # erlang:'=<'/2
  #!# SIMPLIFIED (Only supports numbers)
  def _json_bif_lteq_to_z3(self, term1, term2, term3):
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    ax = self._term_comparison("=<", t1, t2, t3)
    self.solver.add(ax)
  
  def _term_comparison(self, op, x, y, z):
    Tm = self.Term
    T = self.atom_true
    F = self.atom_false
    opts_asc = {"<" : T, "=<" : T, ">" : F, ">=" : F}
    opts_desc = {"<" : F, "=<" : F, ">" : T, ">=" : T}
    es = []
    # Int - Int
    es.append(And(
      Tm.is_int(x),
      Tm.is_int(y),
      If(self._comp_h(op, Tm.ival(x), Tm.ival(y)) , z == T, z == F)
    ))
    # Float - Float
    es.append(And(
      Tm.is_real(x),
      Tm.is_real(y),
      If(self._comp_h(op, Tm.rval(x), Tm.rval(y)), z == T, z == F)
    ))
    # Int - Float
    es.append(And(
      Tm.is_int(x),
      Tm.is_real(y),
      If(self._comp_h(op, Tm.ival(x), Tm.rval(y)) , z == T, z == F)
    ))
    # Float - Int
    es.append(And(
      Tm.is_real(x),
      Tm.is_int(y),
      If(self._comp_h(op, Tm.rval(x), Tm.ival(y)) , z == T, z == F)
    ))
    # (Int or Float) - (Atom or Tuple or List)
    es.append(And(
      Or(Tm.is_int(x), Tm.is_real(x)),
      Or(Tm.is_atm(y), Tm.is_lst(y), Tm.is_tpl(y)),
      z == opts_asc[op]
    ))
    # (Atom or Tuple or List) - (Int or Float)
    es.append(And(
      Or(Tm.is_atm(x), Tm.is_lst(x), Tm.is_tpl(x)),
      Or(Tm.is_int(y), Tm.is_real(y)),
      z == opts_desc[op]
    ))
    # Atom - (Tuple or List)
    es.append(And(
      Tm.is_atm(x),
      Or(Tm.is_lst(y), Tm.is_tpl(y)),
      z == opts_asc[op]
    ))
    # (Tuple or List) - Atom
    es.append(And(
      Or(Tm.is_lst(x), Tm.is_tpl(x)),
      Tm.is_atm(y),
      z == opts_desc[op]
    ))
    # Tuple - List
    es.append(And(
      Tm.is_tpl(x),
      Tm.is_lst(y),
      z == opts_asc[op]
    ))
    # List - Tuple
    es.append(And(
      Tm.is_lst(x),
      Tm.is_tpl(y),
      z == opts_desc[op]
    ))
    # Missing (Atom - Atom) & (Tuple - Tuple) & (List - List)
    return Or(*es)

  def _comp_h(self, op, x, y):
    if op == ">":
      return x > y
    elif op == "<":
      return x < y
    elif op == "=<":
      return x <= y
    elif op == ">=":
      return x >= y
  
  # erlang:element/2
  #!# SIMPLIFIED (Expect term1 to represent an Integer)
  def _json_bif_elem_to_z3(self, term1, term2, term3):
    s = self.solver
    l = term1["v"] # Simplification
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    s.add(self.Term.is_tpl(t2))
    t = self.Term.tval(t2)
    for i in range(0, l):
      s.add(self.List.is_cons(t))
      h = self.List.hd(t)
      t = self.List.tl(t)
    s.add(t3 == h)
  
  
  # erlang:float/1
  def _json_bif_float_to_z3(self, term1, term2):
    T = self.Term
    s = self.solver
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    s.add(T.is_real(t2))
    s.add(Or(
      And(T.is_real(t1), t2 == t1),
      And(T.is_int(t1), T.rval(t2) == ToReal(T.ival(t1)))
    ))
  
  # erlang:is_atom/1
  def _json_bif_is_atom_to_z3(self, term1, term2):
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    self.solver.add(If(
      self.Term.is_atm(t1),
      t2 == self.atom_true,
      t2 == self.atom_false
    ))
  
  # erlang:is_float/1
  def _json_bif_is_float_to_z3(self, term1, term2):
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    self.solver.add(If(
      self.Term.is_real(t1),
      t2 == self.atom_true,
      t2 == self.atom_false
    ))
  
  # erlang:is_integer/1
  def _json_bif_is_integer_to_z3(self, term1, term2):
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    self.solver.add(If(
      self.Term.is_int(t1),
      t2 == self.atom_true,
      t2 == self.atom_false
    ))
  
  # erlang:is_list/1
  def _json_bif_is_list_to_z3(self, term1, term2):
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    self.solver.add(If(
      self.Term.is_lst(t1),
      t2 == self.atom_true,
      t2 == self.atom_false
    ))
  
  # erlang:is_tuple/1
  def _json_bif_is_tuple_to_z3(self, term1, term2):
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    self.solver.add(If(
      self.Term.is_tpl(t1),
      t2 == self.atom_true,
      t2 == self.atom_false
    ))
  
  # erlang:is_boolean/1
  def _json_bif_is_boolean_to_z3(self, term1, term2):
    T = self.atom_true
    F = self.atom_false
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    self.solver.add(If(
      Or(t1 == T, t1 == F),
      t2 == T,
      t2 == F
    ))
  
  # erlang:is_number/1
  def _json_bif_is_number_to_z3(self, term1, term2):
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    self.solver.add(If(
      Or(self.Term.is_int(t1), self.Term.is_real(t1)),
      t2 == self.atom_true,
      t2 == self.atom_false
    ))
  
  # erlang:trunc/1
  def _json_bif_trunc_to_z3(self, term1, term2):
    T = self.Term
    s = self.solver
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    s.add(T.is_int(t2))
    e1 = And(T.is_int(t1), t2 == t1)
    e2 = And(T.is_real(t1), If(
      T.rval(t1) >= 0.0,
      T.ival(t2) == ToInt(T.rval(t1)),
      T.ival(t2) == ToInt(T.rval(t1)) + 1,
    ))
    s.add(Or(e1, e2))
  
  # erlang:round/1
  def _json_bif_round_to_z3(self, term1, term2):
    T = self.Term
    s = self.solver
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    s.add(T.is_int(t2))
    e1 = And(T.is_int(t1), t2 == t1)
    t = ToInt(T.rval(t1))
    e2 = And(T.is_real(t1), If(
      T.rval(t1) - t >= 0.5,
      T.ival(t2) == t + 1,
      T.ival(t2) == t
    ))
    s.add(Or(e1, e2))
  
  # erlang:list_to_tuple/1
  def _json_bif_list_to_tuple_to_z3(self, term1, term2):
    T = self.Term
    s = self.solver
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    s.add(T.is_lst(t1))
    s.add(T.is_tpl(t2))
    s.add(T.lval(t1) == T.tval(t2))
  
  # erlang:tuple_to_list/1
  def _json_bif_tuple_to_list_to_z3(self, term1, term2):
    T = self.Term
    s = self.solver
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    s.add(T.is_tpl(t1))
    s.add(T.is_lst(t2))
    s.add(T.tval(t1) == T.lval(t2))
  
  # erlang:length/1
  def _json_bif_length_to_z3(self, term1, term2):
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    ax = self._bif_len_h("list", t1, t2)
    self.solver.add(ax)
  
  # erlang:tuple_size/1
  def _json_bif_tuple_size_to_z3(self, term1, term2):
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    ax = self._bif_len_h("tuple", t1, t2)
    self.solver.add(ax)
  
  # Helper function for erlang:length/1 & erlang:tuple_size/1
  def _bif_len_h(self, typ, t, n):
    T = self.Term
    L = self.List
    if typ == "list":
      e = T.is_lst(t)
      t = T.lval(t)
    elif typ == "tuple":
      e = T.is_tpl(t)
      t = T.tval(t)
    es = []
    i = 0
    while i <= self.max_len:
      es.append((t, i))
      t = L.tl(t)
      i += 1
    ax = (T.ival(n) >= i)
    for (x, i) in reversed(es):
      ax = If(
        L.is_nil(x),
        n == T.int(i),
        And(L.is_cons(x), ax)
      )
    ax = And(e, T.is_int(n), ax)
    return simplify(ax)
  
  # erlang:make_tuple/2
  def _json_bif_make_tuple_2_to_z3(self, term1, term2, term3):
    t1 = self.json_term_to_z3(term1)
    t2 = self.json_term_to_z3(term2)
    t3 = self.json_term_to_z3(term3)
    ax = self._make_tuple_h(t1, t2, t3)
    self.solver.add(ax)
  
  # Helper function for erlang:make_tuple/2
  def _make_tuple_h(self, x, n, y):
    T = self.Term
    L = self.List
    t = L.nil
    es = [And(n == T.int(0), y == T.tpl(t))]
    for i in range(1, self.max_len+1):
      t = L.cons(x, t)
      es.append(And(n == T.int(i), y == T.tpl(t)))
    es.append(T.ival(n) > self.max_len)
    ax = And(T.is_int(n), Or(*es))
    return simplify(ax)
  
  ## Decode the Z3 solution to JSON
  def z3_solution_to_json(self):
    sol = {}
    for s in self.env.params:
      sol[s] = self.z3_param_to_json(s)
    return sol
  
  def z3_param_to_json(self, s):
    x = self.env.lookup(s)
    v = self.model[x]
    if (v is None):
      return "any"
    else:
      return self.z3_term_to_json(v)
      
  
