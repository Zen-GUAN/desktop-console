# WinCtlPanel2 native host. UTF-8 with BOM is required by PowerShell 5.1.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;
using System.Collections.Generic;
using System.Windows.Forms;
using System.Drawing;

public class Dxva2 {
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int left, top, right, bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct PHYSICAL_MONITOR {
        public IntPtr hPhysicalMonitor;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst=128)] public char[] szPhysicalMonitorDescription;
    }
    public delegate bool MonitorEnumProc(IntPtr hMonitor, IntPtr hdc, ref RECT rect, IntPtr data);
    [DllImport("user32.dll")] public static extern bool EnumDisplayMonitors(IntPtr hdc, IntPtr clip, MonitorEnumProc proc, IntPtr data);
    [DllImport("dxva2.dll", SetLastError=true)] public static extern bool GetNumberOfPhysicalMonitorsFromHMONITOR(IntPtr hMonitor, out uint count);
    [DllImport("dxva2.dll", SetLastError=true)] public static extern bool GetPhysicalMonitorsFromHMONITOR(IntPtr hMonitor, uint count, [Out] PHYSICAL_MONITOR[] monitors);
    [DllImport("dxva2.dll", SetLastError=true)] public static extern bool GetMonitorBrightness(IntPtr hMonitor, out uint min, out uint cur, out uint max);
    [DllImport("dxva2.dll", SetLastError=true)] public static extern bool SetMonitorBrightness(IntPtr hMonitor, uint value);
    [DllImport("dxva2.dll")] public static extern bool DestroyPhysicalMonitor(IntPtr hMonitor);
    public static List<IntPtr> Handles = new List<IntPtr>();
    private static bool Callback(IntPtr hMonitor, IntPtr hdc, ref RECT rect, IntPtr data) { Handles.Add(hMonitor); return true; }
    public static int Enumerate() { Handles.Clear(); return EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, new MonitorEnumProc(Callback), IntPtr.Zero) ? Handles.Count : -1; }
}
public class W2 {
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern bool SystemParametersInfo(uint action, uint param, StringBuilder value, uint flags);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern bool SystemParametersInfo(uint action, uint param, string value, uint flags);
    [DllImport("user32.dll")] public static extern bool ReleaseCapture();
    [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hwnd, int msg, IntPtr wparam, IntPtr lparam);
    [DllImport("user32.dll")] public static extern IntPtr SendMessageTimeout(IntPtr hwnd, uint msg, IntPtr wparam, string lparam, uint flags, uint timeout, out IntPtr result);
    [DllImport("user32.dll")] public static extern bool RegisterHotKey(IntPtr hwnd, int id, uint modifiers, uint key);
    [DllImport("user32.dll")] public static extern bool UnregisterHotKey(IntPtr hwnd, int id);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr data);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hwnd);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int maxCount);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hwnd, int command);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int width, int height, uint flags);
    public delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr data);
    public static readonly IntPtr HWND_TOPMOST = new IntPtr(-1), HWND_NOTOPMOST = new IntPtr(-2);
}
public class WinCtlWindow : NativeWindow, IWin32Window {
    public event EventHandler<int> HotKey;
    public event EventHandler Closing;
    public WinCtlWindow() { CreateHandle(new CreateParams()); }
    protected override void WndProc(ref Message m) {
        if (m.Msg == 0x0312) { EventHandler<int> h = HotKey; if (h != null) h(this, m.WParam.ToInt32()); }
        if (m.Msg == 0x0010) { EventHandler h = Closing; if (h != null) h(this, EventArgs.Empty); }
        base.WndProc(ref m);
    }
    public void BeginTitleDrag(IntPtr target) { W2.ReleaseCapture(); W2.SendMessage(target, 0xA1, new IntPtr(2), IntPtr.Zero); }
    public void BeginResize(IntPtr target) { W2.ReleaseCapture(); W2.SendMessage(target, 0xA1, new IntPtr(8), IntPtr.Zero); }
}
public class TrayIcon : IDisposable {
    private NotifyIcon icon;
    public event EventHandler ShowRequested, ExitRequested; public event EventHandler<bool> TopmostRequested;
    public TrayIcon() {
        Bitmap bitmap = new Bitmap(32, 32);
        using (Graphics g = Graphics.FromImage(bitmap)) {
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            using (SolidBrush b = new SolidBrush(Color.FromArgb(139,157,255))) g.FillEllipse(b, 2, 2, 28, 28);
            using (Pen p = new Pen(Color.FromArgb(24,29,48), 3)) {
                p.StartCap = p.EndCap = System.Drawing.Drawing2D.LineCap.Round;
                g.DrawLines(p, new Point[]{new Point(9,11),new Point(13,22),new Point(16,15),new Point(20,22),new Point(24,10)});
            }
        }
        icon = new NotifyIcon(); icon.Icon = Icon.FromHandle(bitmap.GetHicon()); icon.Text = "WinCtl 2 - Desktop"; icon.Visible = true;
        ContextMenuStrip menu = new ContextMenuStrip();
        menu.Items.Add("Show panel", null, delegate { if (ShowRequested != null) ShowRequested(this, EventArgs.Empty); });
        ToolStripMenuItem top = new ToolStripMenuItem("Always on top"); top.CheckOnClick = true;
        top.Click += delegate { if (TopmostRequested != null) TopmostRequested(this, top.Checked); }; menu.Items.Add(top);
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Exit (keep changes)", null, delegate { if (ExitRequested != null) ExitRequested(this, EventArgs.Empty); });
        icon.ContextMenuStrip = menu;
        icon.MouseClick += delegate(object s, MouseEventArgs e) { if (e.Button == MouseButtons.Left && ShowRequested != null) ShowRequested(this, EventArgs.Empty); };
    }
    public void Dispose() { if (icon != null) { icon.Visible = false; icon.Dispose(); icon = null; } }
}
"@ -ReferencedAssemblies @('System.Windows.Forms', 'System.Drawing')