import cpp
import semmle.code.cpp.pointsto.CallGraph

from Function caller, Function callee, FunctionCall call
where
  call.getEnclosingFunction() = caller and
  call.getTarget()            = callee  and
  caller.hasDefinition()  and
  callee.hasDefinition()
select
  /* caller */
  caller.getName()                         as caller_name,
  caller.getFile().getAbsolutePath()       as caller_path,
  caller.getLocation().getStartLine() as caller_start_line,
  caller.getLocation().getStartColumn() as caller_start_col,
  caller.getBlock().getLocation().getEndLine() as caller_end_line,
  caller.getBlock().getLocation().getEndColumn() as caller_end_col,

  /* callee */
  callee.getName()                         as callee_name,
  callee.getFile().getAbsolutePath()       as callee_path,
  callee.getLocation().getStartLine() as callee_start_line,
  callee.getLocation().getStartColumn() as callee_start_col,
  callee.getBlock().getLocation().getEndLine() as callee_end_line,
  callee.getBlock().getLocation().getEndColumn() as callee_end_col,

  call.getLocation()                       as call_loc
