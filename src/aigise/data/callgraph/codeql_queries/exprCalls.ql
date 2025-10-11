import cpp

from Function f, ExprCall call, int argIdx
where call.getEnclosingFunction() = f
select
  f.getLocation() as id,
  call.getLocation() as cid,
  argIdx,
  call.getArgument(argIdx).getType() as arg,
  f.getLocation().getStartLine() as start_line,
  f.getLocation().getStartColumn() as start_col,
  f.getBlock().getLocation().getEndLine() as end_line,
  f.getBlock().getLocation().getEndColumn() as end_col,
  f.getFile().getAbsolutePath() as caller_path,
  f.getName() as name
