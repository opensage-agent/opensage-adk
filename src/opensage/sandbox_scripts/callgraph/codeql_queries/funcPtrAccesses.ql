import cpp

boolean isGlobal(FunctionAccess fa) {
  exists(Function f | fa.getEnclosingFunction() = f) and result = false
  or
  not exists(Function f | fa.getEnclosingFunction() = f) and result = true
}

string enclosingVar(FunctionAccess fa){
  exists(Variable v | fa.getEnclosingVariable() = v and result = v.getLocation().toString())
  or
  not exists(Variable v | fa.getEnclosingVariable() = v) and result = ""
}


from FunctionAccess fa, Function dst
where fa.getTarget() = dst
select
  dst.getName() as name,
  dst.getLocation() as loc,
  dst.getParameterString() as param,
  isGlobal(fa) as isGlobal,
  enclosingVar(fa) as enclosingVar,
  dst.getLocation().getStartLine() as start_line,
  dst.getLocation().getStartColumn() as start_col,
  dst.getBlock().getLocation().getEndLine() as end_line,
  dst.getBlock().getLocation().getEndColumn() as end_col,
  dst.getFile().getAbsolutePath() as callee_path
