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
  caller.getLocation().getStartLine() as caller_start,
  caller.getBlock().getLocation().getEndLine() as caller_end,

  /* callee */
  callee.getName()                         as callee_name,
  callee.getFile().getAbsolutePath()       as callee_path,
  callee.getLocation().getStartLine() as callee_start,
  callee.getBlock().getLocation().getEndLine() as callee_end,

  call.getLocation()                       as call_loc
