param([string]$OutDir)
Add-Type -TypeDefinition @"
using System;
using System.Reflection;
using System.Reflection.Emit;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
public static class TypeLibs {
    [DllImport("oleaut32.dll", CharSet = CharSet.Unicode)]
    static extern int LoadTypeLibEx(string file, int kind, out ITypeLib lib);
    class Sink : ITypeLibImporterNotifySink {
        public void ReportEvent(ImporterEventKind kind, int code, string message) {}
        public Assembly ResolveRef(object lib) { return null; }
    }
    public static void Import(string path, string outDir) {
        ITypeLib lib;
        Marshal.ThrowExceptionForHR(LoadTypeLibEx(path, 0, out lib));
        Environment.CurrentDirectory = outDir;
        var asm = (AssemblyBuilder)new TypeLibConverter().ConvertTypeLibToAssembly(
            lib, "Interop.UIAutomationClient.dll", 0, new Sink(), null, null, "Interop.UIAutomationClient", null);
        asm.Save("Interop.UIAutomationClient.dll");
    }
}
"@
[TypeLibs]::Import("$env:WINDIR\System32\UIAutomationCore.dll", (Resolve-Path $OutDir).Path)
Get-Item (Join-Path $OutDir "Interop.UIAutomationClient.dll")
