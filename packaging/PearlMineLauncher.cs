// SPDX-License-Identifier: Apache-2.0
// Local source launcher. No downloads, installation, services, elevated rights or mining.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;
using Microsoft.Win32;
internal static class PearlMineLauncher {
  [STAThread] static int Main(string[] args) {
    Application.EnableVisualStyles();
    string root=AppDomain.CurrentDomain.BaseDirectory;
    if (!File.Exists(Path.Combine(root,"boris_pearl","__main__.py"))) {
      MessageBox.Show("Положите PearlMine.exe рядом с папкой boris_pearl из архива проекта.","PearlMine");return 2;
    }
    foreach(string candidate in Candidates(root)) {
      try {
        if (!File.Exists(candidate) || candidate.IndexOf(@"\Microsoft\WindowsApps\",StringComparison.OrdinalIgnoreCase)>=0) continue;
        var probe=new ProcessStartInfo(candidate,"-c \"import sys,tkinter;sys.exit(0 if sys.version_info>=(3,11) else 2)\"");
        probe.UseShellExecute=false;probe.CreateNoWindow=true;probe.RedirectStandardError=true;probe.RedirectStandardOutput=true;
        using(var test=Process.Start(probe)) { if (!test.WaitForExit(15000)) {test.Kill();continue;} if(test.ExitCode!=0)continue; }
        string command=args.Length==1 && args[0]=="--agent" ? "-m boris_pearl.agent_ui" : "-m boris_pearl gui";
        var start=new ProcessStartInfo(candidate,command);
        start.WorkingDirectory=root;start.UseShellExecute=false;start.CreateNoWindow=true;
        start.RedirectStandardError=true;
        start.EnvironmentVariables["PYTHONUTF8"]="1";
        using(var app=Process.Start(start)) {
          string errors=app.StandardError.ReadToEnd();app.WaitForExit();
          if(app.ExitCode!=0) {
            string log=Path.Combine(root,"state","control-center","last-error.log");
            Directory.CreateDirectory(Path.GetDirectoryName(log));File.WriteAllText(log,errors,Encoding.UTF8);
            MessageBox.Show("Приложение завершилось с ошибкой. Подробности:\n"+log,"PearlMine");return app.ExitCode;
          }
        }
        return 0;
      } catch(Exception) { }
    }
    MessageBox.Show("Нужен уже установленный Python 3.11 или новее с Tkinter. Этот предварительный выпуск ничего не скачивает и не устанавливает.\n\nПолностью самостоятельная сборка без Python будет подготовлена отдельно перед выпуском.","PearlMine");
    return 3;
  }
  static IEnumerable<string> Candidates(string root) {
    var paths=new List<string>();
    paths.Add(Path.Combine(root,"Python","python.exe"));
    foreach(var scope in new RegistryHive[]{RegistryHive.CurrentUser,RegistryHive.LocalMachine}) {
      foreach(var view in new RegistryView[]{RegistryView.Registry64,RegistryView.Registry32}) {
        RegistryKey hive=null;RegistryKey core=null;
        try {
          hive=RegistryKey.OpenBaseKey(scope,view);core=hive.OpenSubKey(@"Software\Python\PythonCore");
          if(core!=null) foreach(string version in core.GetSubKeyNames()) {
            using(var install=core.OpenSubKey(version+@"\InstallPath")) {
              if(install==null)continue;
              string executable=install.GetValue("ExecutablePath") as string;
              if(!String.IsNullOrEmpty(executable))paths.Add(executable);
              string directory=install.GetValue(null) as string;
              if(!String.IsNullOrEmpty(directory))paths.Add(Path.Combine(directory,"python.exe"));
            }
          }
        } catch { } finally {if(core!=null)core.Dispose();if(hive!=null)hive.Dispose();}
      }
    }
    foreach(string part in (Environment.GetEnvironmentVariable("PATH")??"").Split(Path.PathSeparator)) {
      try {paths.Add(Path.Combine(part.Trim('"'),"python.exe"));}catch{}
    }
    return paths;
  }
}
