// Compile with the Windows .NET Framework compiler; no global Python is used.
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using System.Reflection;

[assembly: AssemblyTitle("RAG Workbench")]
[assembly: AssemblyProduct("RAG Workbench")]
[assembly: AssemblyDescription("Local document knowledge workspace")]
[assembly: AssemblyVersion("0.1.8.0")]
[assembly: AssemblyFileVersion("0.1.8.0")]

static class Launcher {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)]
    static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll")]
    static extern bool SetInformationJobObject(IntPtr job, int info, IntPtr data, uint length);
    [DllImport("kernel32.dll")]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")]
    static extern bool CloseHandle(IntPtr handle);
    [StructLayout(LayoutKind.Sequential)]
    struct BasicLimits {
        public long ProcessTime, JobTime;
        public uint Flags;
        public UIntPtr MinWorkingSet, MaxWorkingSet;
        public uint ActiveProcesses;
        public UIntPtr Affinity;
        public uint Priority, Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct IoCounters { public ulong A, B, C, D, E, F; }
    [StructLayout(LayoutKind.Sequential)]
    struct ExtendedLimits {
        public BasicLimits Basic;
        public IoCounters Io;
        public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }

    // Correct Windows argv quoting, including paths containing spaces and trailing slashes.
    static string Quote(string value) {
        var result = new System.Text.StringBuilder("\"");
        int slashes = 0;
        foreach (char ch in value) {
            if (ch == '\\') { slashes++; continue; }
            result.Append('\\', ch == '"' ? slashes * 2 + 1 : slashes);
            result.Append(ch); slashes = 0;
        }
        result.Append('\\', slashes * 2); result.Append('"');
        return result.ToString();
    }

    [STAThread]
    static int Main(string[] args) {
        IntPtr job = IntPtr.Zero;
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            var info = new ProcessStartInfo(Path.Combine(root, "runtime", "pythonw.exe"));
            info.Arguments = "-I -B -m rag_workbench.desktop --bundle " + Quote(root);
            foreach (string arg in args) info.Arguments += " " + Quote(arg);
            info.UseShellExecute = false; info.CreateNoWindow = true;
            info.WorkingDirectory = root;
            job = CreateJobObject(IntPtr.Zero, null);
            var limits = new ExtendedLimits();
            // Only explicitly external tools request breakaway; backend children stay owned.
            limits.Basic.Flags = 0x2000 | 0x800; // KILL_ON_JOB_CLOSE | BREAKAWAY_OK
            int length = Marshal.SizeOf(limits);
            IntPtr pointer = Marshal.AllocHGlobal(length);
            try {
                Marshal.StructureToPtr(limits, pointer, false);
                if (job == IntPtr.Zero || !SetInformationJobObject(job, 9, pointer, (uint)length))
                    throw new InvalidOperationException("Cannot initialize process job.");
            } finally { Marshal.FreeHGlobal(pointer); }
            using (Process child = Process.Start(info)) {
                if (!AssignProcessToJobObject(job, child.Handle)) {
                    child.Kill();
                    throw new InvalidOperationException("Cannot attach process job.");
                }
                child.WaitForExit();
                return child.ExitCode;
            }
        } catch (Exception error) {
            MessageBox.Show("无法启动 RAG Workbench（" + error.GetType().Name +
                "）。请完整解压软件文件夹，再运行主程序。", "RAG Workbench",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        } finally { if (job != IntPtr.Zero) CloseHandle(job); }
    }
}
