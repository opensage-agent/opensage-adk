@main def exec(cpgFile: String, outDir: String) = {
    importCpg(cpgFile)
    cpg.method.toJson #> s"$outDir/METHOD.json"
    cpg.call.toJson #> s"$outDir/CALL.json"
    cpg.call.map(c => (c.method.id, c.id)).toJson #> s"$outDir/r_METHOD-CALL-CONTAINS.json"
    cpg.call.flatMap(c => c.callee.map(ca => (c.id, ca.id))).toJson #> s"$outDir/r_CALL-METHOD-CALL.json"
}
