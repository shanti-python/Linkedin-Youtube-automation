"use client";

import { useEffect, useState, useRef, useMemo } from "react";

// API helper utility to connect to backend dynamically
const getApiUrl = () => {
  if (typeof window !== "undefined") {
    const hostname = window.location.hostname;
    const port = window.location.port === "3000" ? "8000" : window.location.port;
    const protocol = window.location.protocol;
    return `${protocol}//${hostname}:${port || "8000"}`;
  }
  return "http://localhost:8000";
};

interface CSVLogEntry {
  post_url: string;
  comment_id: string;
  author: string;
  comment: string;
  reply: string;
  status: "replied" | "skipped" | "error";
  reason: string;
  timestamp: string;
  processing_time: string;
}

interface StatusData {
  status: "idle" | "processing";
  is_processing: boolean;
  current_post_url: string | null;
  processed_today: number;
  max_replies_per_day: number;
  last_run: string | null;
  config_summary: Record<string, any>;
}

interface Toast {
  id: number;
  message: string;
  type: "success" | "error" | "info";
}

export default function Home() {
  const [platform, setPlatform] = useState<"linkedin" | "youtube">("linkedin");
  const [activeTab, setActiveTab] = useState<"dashboard" | "logs">("dashboard");
  const [status, setStatus] = useState<StatusData | null>(null);
  const [csvLogs, setCsvLogs] = useState<CSVLogEntry[]>([]);
  const [appLogs, setAppLogs] = useState<string>("");
  const [selectedLog, setSelectedLog] = useState<CSVLogEntry | null>(null);

  // Global Date Filter State (All Time, Today, Week, Month)
  const [dateFilter, setDateFilter] = useState<string>("all");
  const [accountFilter, setAccountFilter] = useState<string>("all");
  const [postFilter, setPostFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");

  // Configuration Settings State
  const [configSettings, setConfigSettings] = useState<Record<string, any>>({});
  const [isSavingConfig, setIsSavingConfig] = useState<boolean>(false);
  const [saveStatus, setSaveStatus] = useState<{ success: boolean; msg: string } | null>(null);
  const [isStartingBot, setIsStartingBot] = useState<boolean>(false);
  const [isClearingLogs, setIsClearingLogs] = useState<boolean>(false);

  // Toast notifications state
  const [toasts, setToasts] = useState<Toast[]>([]);

  // Custom Confirmation Modal State
  const [confirmModal, setConfirmModal] = useState<{
    isOpen: boolean;
    title: string;
    message: string;
    onConfirm: () => void;
  }>({
    isOpen: false,
    title: "",
    message: "",
    onConfirm: () => { },
  });

  // Console logs auto-scroll
  const terminalEndRef = useRef<HTMLDivElement>(null);
  const [isApiConnected, setIsApiConnected] = useState<boolean>(true);

  // Helper function to dispatch Toast Notifications
  const showToast = (message: string, type: "success" | "error" | "info" = "info") => {
    const id = Date.now();
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  };

  // Load baseline status and logs
  const fetchData = async () => {
    try {
      const apiUrl = getApiUrl();

      // 1. Fetch Status
      const statusRes = await fetch(`${apiUrl}/${platform}/comment/status`);
      if (statusRes.ok) {
        const statusData = await statusRes.json();
        setStatus(statusData);
        setIsApiConnected(true);
        // Sync configuration fields if not modified
        if (Object.keys(configSettings).length === 0 && statusData.config_summary) {
          setConfigSettings(statusData.config_summary);
        }
      }

      // 2. Fetch CSV Logs
      const csvRes = await fetch(`${apiUrl}/${platform}/logs/csv`);
      if (csvRes.ok) {
        const csvData = await csvRes.json();
        setCsvLogs(csvData.reverse());
      }

      // 3. Fetch App Logs if on Logs tab
      if (activeTab === "logs") {
        const appRes = await fetch(`${apiUrl}/${platform}/logs/app?lines=300`);
        if (appRes.ok) {
          const appText = await appRes.json();
          setAppLogs(appText);
        }
      }
    } catch (err) {
      console.error("API Fetch Error:", err);
      setIsApiConnected(false);
    }
  };

  // Poll for status and logs
  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 4000);
    return () => clearInterval(interval);
  }, [activeTab, platform, Object.keys(configSettings).length]);

  // Auto scroll terminal logs
  useEffect(() => {
    if (activeTab === "logs" && terminalEndRef.current) {
      terminalEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [appLogs, activeTab]);

  // Filter logs for stats calculations dynamically based on global Date Filter
  const filteredLogsForStats = useMemo(() => {
    return csvLogs.filter((l) => {
      if (dateFilter !== "all") {
        const limitDate = new Date();
        if (dateFilter === "today") limitDate.setHours(0, 0, 0, 0);
        else if (dateFilter === "week") limitDate.setDate(limitDate.getDate() - 7);
        else if (dateFilter === "month") limitDate.setDate(limitDate.getDate() - 30);

        if (!l.timestamp) return false;
        return new Date(l.timestamp) >= limitDate;
      }
      return true;
    });
  }, [csvLogs, dateFilter]);

  // Compute stats dynamically based on filtered logs
  const stats = useMemo(() => {
    const totalComments = filteredLogsForStats.length;
    const successes = filteredLogsForStats.filter((l) => l.status === "replied").length;
    const skipped = filteredLogsForStats.filter((l) => l.status === "skipped").length;
    const failed = filteredLogsForStats.filter((l) => l.status === "error").length;

    // Filter replies today explicitly
    const startOfToday = new Date();
    startOfToday.setHours(0, 0, 0, 0);
    const todayCount = csvLogs.filter((l) => {
      if (!l.timestamp) return false;
      const d = new Date(l.timestamp);
      return d >= startOfToday && l.status === "replied";
    }).length;

    return {
      totalComments,
      successes,
      skipped,
      failed,
      todayCount,
    };
  }, [filteredLogsForStats, csvLogs]);

  // List of unique posts and accounts for filters
  const filterOptions = useMemo(() => {
    const posts = Array.from(new Set(csvLogs.map((l) => l.post_url))).filter(Boolean);
    const accounts = Array.from(new Set(csvLogs.map((l) => l.author))).filter(Boolean);
    return { posts, accounts };
  }, [csvLogs]);

  // SVG Chart data: Count of replies grouped by date/time depending on Date Filter
  const chartData = useMemo(() => {
    let labels: string[] = [];
    let values: number[] = [];

    if (dateFilter === "today") {
      // Group by 4-hour intervals for today (6 bars)
      const now = new Date();
      now.setHours(0, 0, 0, 0); // start of today

      labels = ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"];
      values = [0, 0, 0, 0, 0, 0];

      csvLogs.forEach((l) => {
        if (!l.timestamp || l.status !== "replied") return;
        const d = new Date(l.timestamp);
        if (d >= now) {
          const hour = d.getHours();
          const bucket = Math.min(5, Math.floor(hour / 4));
          values[bucket]++;
        }
      });
    } else if (dateFilter === "month") {
      // Group by 5-day intervals (6 bars)
      labels = ["26-30d", "21-25d", "16-20d", "11-15d", "6-10d", "1-5d"];
      values = [0, 0, 0, 0, 0, 0];

      const now = new Date();
      csvLogs.forEach((l) => {
        if (!l.timestamp || l.status !== "replied") return;
        const d = new Date(l.timestamp);
        const diffMs = now.getTime() - d.getTime();
        const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
        if (diffDays >= 0 && diffDays < 30) {
          const bucket = Math.min(5, 5 - Math.floor(diffDays / 5));
          if (bucket >= 0 && bucket < 6) {
            values[bucket]++;
          }
        }
      });
    } else if (dateFilter === "all") {
      // Group by last 6 weeks (6 bars)
      labels = ["Week 6", "Week 5", "Week 4", "Week 3", "Week 2", "Week 1"];
      values = [0, 0, 0, 0, 0, 0];

      const now = new Date();
      csvLogs.forEach((l) => {
        if (!l.timestamp || l.status !== "replied") return;
        const d = new Date(l.timestamp);
        const diffMs = now.getTime() - d.getTime();
        const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
        const diffWeeks = Math.floor(diffDays / 7);
        if (diffWeeks >= 0 && diffWeeks < 6) {
          const bucket = 5 - diffWeeks;
          values[bucket]++;
        }
      });
    } else {
      // Default: "week" - Past 7 Days (7 bars)
      labels = Array.from({ length: 7 }, (_, i) => {
        const d = new Date();
        d.setDate(d.getDate() - i);
        return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
      }).reverse();

      values = labels.map((dayStr) => {
        return csvLogs.filter((l) => {
          if (!l.timestamp || l.status !== "replied") return false;
          const dStr = new Date(l.timestamp).toLocaleDateString(undefined, {
            month: "short",
            day: "numeric",
          });
          return dStr === dayStr;
        }).length;
      });
    }

    const maxVal = Math.max(...values, 5);
    return { labels, values, maxVal };
  }, [csvLogs, dateFilter]);

  // Filtered logs list for display table
  const filteredLogs = useMemo(() => {
    return csvLogs.filter((l) => {
      // Date Filter
      if (dateFilter !== "all") {
        const limitDate = new Date();
        if (dateFilter === "today") limitDate.setHours(0, 0, 0, 0);
        else if (dateFilter === "week") limitDate.setDate(limitDate.getDate() - 7);
        else if (dateFilter === "month") limitDate.setDate(limitDate.getDate() - 30);

        if (new Date(l.timestamp) < limitDate) return false;
      }

      // Account Filter
      if (accountFilter !== "all" && l.author !== accountFilter) return false;

      // Post URL Filter
      if (postFilter !== "all" && l.post_url !== postFilter) return false;

      // Search Query
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const matchesAuthor = l.author?.toLowerCase().includes(q);
        const matchesComment = l.comment?.toLowerCase().includes(q);
        const matchesReply = l.reply?.toLowerCase().includes(q);
        const matchesReason = l.reason?.toLowerCase().includes(q);
        if (!matchesAuthor && !matchesComment && !matchesReply && !matchesReason) return false;
      }

      return true;
    });
  }, [csvLogs, dateFilter, accountFilter, postFilter, searchQuery]);

  // Unified Run Controls
  const handleStartBot = async () => {
    setIsStartingBot(true);
    try {
      const apiUrl = getApiUrl();

      // Persist current settings (such as Google Sheet Rules URL) to backend before starting
      if (Object.keys(configSettings).length > 0) {
        try {
          await fetch(`${apiUrl}/${platform}/config/save`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ settings: configSettings }),
          });
        } catch (saveErr) {
          console.warn("Could not pre-save settings before starting bot:", saveErr);
        }
      }

      const res = await fetch(`${apiUrl}/${platform}/comment/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings: configSettings }),
      });
      if (res.ok) {
        showToast("Bot automation started successfully!", "success");
        fetchData();
      } else {
        const errData = await res.json().catch(() => ({}));
        showToast(errData.detail || "Failed to start bot. Check settings or credentials.", "error");
      }
    } catch (err) {
      showToast("Error contacting API: " + err, "error");
    } finally {
      setIsStartingBot(false);
    }
  };

  const handleStopBot = async () => {
    try {
      const apiUrl = getApiUrl();
      const res = await fetch(`${apiUrl}/${platform}/comment/stop`, { method: "POST" });
      if (res.ok) {
        showToast("Bot automation stop request sent.", "info");
        fetchData();
      }
    } catch (err) {
      console.error("Stop Bot Request Failed:", err);
    }
  };

  // Truncate logs/app.log via custom confirmation Modal flow
  const handleClearLogs = () => {
    setConfirmModal({
      isOpen: true,
      title: "Clear Logs",
      message: "Are you sure you want to permanently clear all app logs on the backend? This action cannot be undone.",
      onConfirm: async () => {
        setIsClearingLogs(true);
        try {
          const apiUrl = getApiUrl();
          const res = await fetch(`${apiUrl}/${platform}/logs/clear`, { method: "POST" });
          if (res.ok) {
            setAppLogs("");
            showToast("System logs cleared!", "success");
          } else {
            showToast("Failed to clear backend logs.", "error");
          }
        } catch (err) {
          showToast("Error clearing logs: " + err, "error");
        } finally {
          setIsClearingLogs(false);
          setConfirmModal((prev) => ({ ...prev, isOpen: false }));
        }
      }
    });
  };

  // Save Settings
  const handleSaveConfig = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSavingConfig(true);
    setSaveStatus(null);
    try {
      const apiUrl = getApiUrl();
      const res = await fetch(`${apiUrl}/${platform}/config/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings: configSettings }),
      });
      if (res.ok) {
        showToast("Settings saved successfully!", "success");
        setSaveStatus({ success: true, msg: "Settings saved successfully!" });
        fetchData();
      } else {
        showToast("Failed to save settings.", "error");
        setSaveStatus({ success: false, msg: "Failed to update settings on backend." });
      }
    } catch (err) {
      showToast("Network error saving settings.", "error");
      setSaveStatus({ success: false, msg: "Network error saving settings: " + err });
    } finally {
      setIsSavingConfig(false);
    }
  };

  const handleConfigChange = (key: string, val: any) => {
    setConfigSettings((prev) => ({
      ...prev,
      [key]: val,
    }));
  };

  // Helper for status badge formatting
  const getStatusColor = () => {
    if (!isApiConnected) return "bg-red-500/20 text-red-400 border-red-500/30";
    if (status?.is_processing) return "bg-emerald-500/20 text-emerald-400 border-emerald-500/30";
    return "bg-slate-500/20 text-slate-400 border-slate-500/30";
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#0f111a] text-slate-100 font-sans">

      {/* LEFT SIDEBAR NAVIGATION */}
      <aside className="w-72 bg-[#161925] flex flex-col border-r border-slate-800">

        {/* Branding header */}
        <div className="p-5 border-b border-slate-800">
          <div className="flex items-center gap-3 mb-4">
            <div className={`w-8 h-8 rounded-lg flex items-center justify-center font-bold text-white shadow-lg ${platform === 'linkedin' ? 'bg-indigo-500 shadow-indigo-500/30' : 'bg-red-500 shadow-red-500/30'}`}>
              {platform === 'linkedin' ? 'L' : 'Y'}
            </div>
            <div>
              <h1 className="font-semibold text-white tracking-wide text-sm leading-none uppercase">
                {platform === 'linkedin' ? 'LinkedIn Bot' : 'YouTube Bot'}
              </h1>
              <span className="text-[10px] text-slate-500 uppercase tracking-widest font-semibold mt-1 block">Comment Monitor</span>
            </div>
          </div>

          {/* Platform Switcher Tabs */}
          <div className="flex rounded-lg overflow-hidden border border-slate-700 text-xs font-bold">
            <button
              onClick={() => { setPlatform("linkedin"); setConfigSettings({}); setStatus(null); }}
              className={`flex-1 py-2 flex items-center justify-center gap-1.5 transition-all cursor-pointer ${platform === "linkedin"
                  ? "bg-indigo-600 text-white"
                  : "bg-[#0f111a] text-slate-400 hover:text-white hover:bg-slate-800"
                }`}
            >
              <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M16 8a6 6 0 016 6v7h-4v-7a2 2 0 00-2-2 2 2 0 00-2 2v7h-4v-7a6 6 0 016-6zM2 9h4v12H2z" />
                <circle cx="4" cy="4" r="2" />
              </svg>
              LinkedIn
            </button>
            <button
              onClick={() => { setPlatform("youtube"); setConfigSettings({}); setStatus(null); }}
              className={`flex-1 py-2 flex items-center justify-center gap-1.5 transition-all cursor-pointer ${platform === "youtube"
                  ? "bg-red-600 text-white"
                  : "bg-[#0f111a] text-slate-400 hover:text-white hover:bg-slate-800"
                }`}
            >
              <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M23.495 6.205a3.007 3.007 0 00-2.088-2.088c-1.87-.501-9.396-.501-9.396-.501s-7.507-.01-9.396.501A3.007 3.007 0 00.527 6.205a31.247 31.247 0 00-.522 5.805 31.247 31.247 0 00.522 5.783 3.007 3.007 0 002.088 2.088c1.868.502 9.396.502 9.396.502s7.506 0 9.396-.502a3.007 3.007 0 002.088-2.088 31.247 31.247 0 00.5-5.783 31.247 31.247 0 00-.5-5.805zM9.609 15.601V8.408l6.264 3.602z" />
              </svg>
              YouTube
            </button>
          </div>
        </div>

        {/* Live Status box (without start/stop button) */}
        <div className="mx-4 my-6 p-4 rounded-xl bg-[#1e2235] border border-slate-800 flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-slate-400 font-bold uppercase tracking-wider">Bot Status</span>
            <div className={`px-2 py-0.5 rounded-full text-[10px] font-bold border uppercase ${getStatusColor()}`}>
              {(!isApiConnected) ? "DISCONNECTED" : (status?.is_processing ? "● RUNNING" : "● IDLE")}
            </div>
          </div>

          <div className="text-xs text-slate-400 break-words leading-relaxed min-h-[36px]">
            {(!isApiConnected) ? (
              <span className="text-red-400">Cannot reach backend API. Make sure uvicorn is running.</span>
            ) : status?.is_processing ? (
              <div>
                Processing comments...
                {status.current_post_url && (
                  <a
                    href={status.current_post_url}
                    target="_blank"
                    rel="noreferrer"
                    className={`hover:underline block truncate mt-1 text-[11px] ${platform === 'youtube' ? 'text-red-400' : 'text-indigo-400'}`}
                  >
                    {status.current_post_url}
                  </a>
                )}
              </div>
            ) : (
              <div>
                {platform === "linkedin" ? (
                  <>Target: <strong className="text-emerald-400">Account posts scan</strong>
                    {configSettings.max_days && (
                      <span className="block text-[10px] text-slate-500 mt-1">Filters: Past {configSettings.max_days} days</span>
                    )}</>
                ) : (
                  <>Target: <strong className="text-red-400">YouTube Shorts</strong>
                    <span className="block text-[10px] text-slate-500 mt-1">Keyword-based auto-reply</span></>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Nav tabs list */}
        <nav className="flex-1 px-4 space-y-1">
          <button
            onClick={() => setActiveTab("dashboard")}
            className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-semibold transition-all cursor-pointer ${activeTab === "dashboard"
                ? platform === "youtube" ? "bg-red-600 text-white shadow-md shadow-red-600/20" : "bg-indigo-600 text-white shadow-md shadow-indigo-600/20"
                : "text-slate-400 hover:bg-slate-800 hover:text-white"
              }`}
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2H6a2 2 0 01-2-2v-4zM14 16a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2h-2a2 2 0 01-2-2v-4z" />
            </svg>
            Dashboard
          </button>

          <button
            onClick={() => setActiveTab("logs")}
            className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-semibold transition-all cursor-pointer ${activeTab === "logs"
                ? platform === "youtube" ? "bg-red-600 text-white shadow-md shadow-red-600/20" : "bg-indigo-600 text-white shadow-md shadow-indigo-600/20"
                : "text-slate-400 hover:bg-slate-800 hover:text-white"
              }`}
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
            Live Logs
          </button>
        </nav>
      </aside>

      {/* MAIN CONTAINER */}
      <main className="flex-1 flex flex-col overflow-hidden bg-[#0b0c13]">

        {/* HEADER BAR */}
        <header className="h-16 border-b border-slate-800 px-8 flex justify-between items-center bg-[#10121d]">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-bold text-white uppercase tracking-wider">
              Automation Dashboard
            </h2>
            <span className={`px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider border ${platform === "youtube"
                ? "bg-red-500/15 text-red-400 border-red-500/25"
                : "bg-indigo-500/15 text-indigo-400 border-indigo-500/25"
              }`}>
              {platform === "youtube" ? "YouTube" : "LinkedIn"}
            </span>
          </div>

          <div className="flex items-center gap-4">
            <div className={`w-1.5 h-1.5 rounded-full animate-ping ${platform === "youtube" ? "bg-red-500" : "bg-emerald-500"}`}></div>
          </div>
        </header>

        {/* TAB CONTENTS SCROLLABLE VIEW */}
        <div className="flex-1 overflow-y-auto p-8">

          {/* TAB 1: DASHBOARD */}
          {activeTab === "dashboard" && (
            <div className="space-y-8 max-w-7xl mx-auto">

              {/* GLOBAL DATE FILTER COMPONENT */}
              <div className="flex justify-between items-center bg-[#151824] p-4 rounded-xl border border-slate-800/80 shadow-sm">
                <div className="flex items-center gap-3">
                  <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">Date Filter:</span>
                  <select
                    value={dateFilter}
                    onChange={(e) => setDateFilter(e.target.value)}
                    className="text-xs bg-[#0b0c13] text-white border border-slate-800 rounded-lg py-1.5 px-3 outline-none cursor-pointer focus:border-indigo-500 transition-colors"
                  >
                    <option value="all">All Time</option>
                    <option value="today">Today Only</option>
                    <option value="week">Past 7 Days</option>
                    <option value="month">Past 30 Days</option>
                  </select>
                </div>
              </div>

              {/* TOP STATS CARDS GRID */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">

                <div className="bg-[#151824] rounded-2xl p-6 border border-slate-800/80 shadow-md">
                  <div className="flex justify-between items-center">
                    <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Replies Today</span>
                    <div className="p-2 rounded-xl bg-indigo-500/10 text-indigo-400">
                      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                    </div>
                  </div>
                  <div className="mt-4">
                    <span className="text-3xl font-extrabold text-white">{stats.todayCount}</span>
                    <p className="text-[11px] text-slate-500 mt-1">Processed since midnight</p>
                  </div>
                </div>

                <div className="bg-[#151824] rounded-2xl p-6 border border-slate-800/80 shadow-md">
                  <div className="flex justify-between items-center">
                    <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Total Replied</span>
                    <div className="p-2 rounded-xl bg-emerald-500/10 text-emerald-400">
                      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                    </div>
                  </div>
                  <div className="mt-4">
                    <span className="text-3xl font-extrabold text-white">{stats.successes}</span>
                    <span className="text-xs text-slate-400 ml-2">({stats.totalComments > 0 ? Math.round((stats.successes / stats.totalComments) * 100) : 0}%)</span>
                    <p className="text-[11px] text-slate-500 mt-1">Success comments posted</p>
                  </div>
                </div>

                <div className="bg-[#151824] rounded-2xl p-6 border border-slate-800/80 shadow-md">
                  <div className="flex justify-between items-center">
                    <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Skipped</span>
                    <div className="p-2 rounded-xl bg-amber-500/10 text-amber-400">
                      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                      </svg>
                    </div>
                  </div>
                  <div className="mt-4">
                    <span className="text-3xl font-extrabold text-white">{stats.skipped}</span>
                    <span className="text-xs text-slate-400 ml-2">({stats.totalComments > 0 ? Math.round((stats.skipped / stats.totalComments) * 100) : 0}%)</span>
                    <p className="text-[11px] text-slate-500 mt-1">Ignored CFBR or duplicate bots</p>
                  </div>
                </div>

              </div>

              {/* AUTOMATION SETTINGS CARD */}
              <div className="bg-[#151824] border border-slate-800/80 rounded-2xl p-8 shadow-md">
                <div className="flex items-center gap-3 border-b border-slate-800 pb-4 mb-6">
                  <div className="p-2 bg-indigo-500/10 rounded-lg text-indigo-400">
                    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                    </svg>
                  </div>
                  <div>
                    <h3 className="text-sm font-bold text-white uppercase tracking-wider leading-none">Automation Settings</h3>
                    <p className="text-[11px] text-slate-500 mt-1">Configure your LLM credentials and targeting constraints</p>
                  </div>
                </div>

                <form onSubmit={handleSaveConfig} className="space-y-6">
                  {saveStatus && (
                    <div className={`p-4 rounded-xl border text-xs font-semibold ${saveStatus.success
                        ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                        : "bg-rose-500/10 text-rose-400 border-rose-500/20"
                      }`}>
                      {saveStatus.msg}
                    </div>
                  )}

                  {platform === "linkedin" ? (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                      <div>
                        <label className="text-xs font-bold text-slate-400 block mb-1">Groq API Key</label>
                        <input
                          type="text"
                          value={configSettings.groq_api_key || ""}
                          onChange={(e) => handleConfigChange("groq_api_key", e.target.value)}
                          placeholder="gsk_..."
                          className="w-full text-xs bg-[#0b0c13] text-white border border-slate-800 focus:border-indigo-500 rounded-lg p-3 outline-none transition-colors font-mono"
                        />
                      </div>
                      <div>
                        <label className="text-xs font-bold text-slate-400 block mb-1">Post Age Limit</label>
                        <select
                          value={configSettings.max_days ?? 30}
                          onChange={(e) => handleConfigChange("max_days", parseInt(e.target.value) || 30)}
                          className="w-full text-xs bg-[#0b0c13] text-white border border-slate-800 focus:border-indigo-500 rounded-lg p-3 outline-none transition-colors cursor-pointer"
                        >
                          {Array.from({ length: 30 }, (_, i) => i + 1).map((day) => (
                            <option key={day} value={day}>
                              {day} {day === 1 ? "Day" : "Days"}
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-4">
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div>
                          <label className="text-xs font-bold text-slate-400 block mb-1">LLM Provider</label>
                          <select
                            value={configSettings.llm_provider || "openai"}
                            onChange={(e) => handleConfigChange("llm_provider", e.target.value)}
                            className="w-full text-xs bg-[#0b0c13] text-white border border-slate-800 focus:border-red-500 rounded-lg p-3 outline-none transition-colors cursor-pointer"
                          >
                            <option value="openai">OpenAI</option>
                            <option value="groq">Groq</option>
                            <option value="ollama">Ollama (Local)</option>
                          </select>
                        </div>
                        <div>
                          <label className="text-xs font-bold text-slate-400 block mb-1">LLM Model</label>
                          <input
                            type="text"
                            value={configSettings.llm_model || ""}
                            onChange={(e) => handleConfigChange("llm_model", e.target.value)}
                            placeholder="gpt-4o-mini"
                            className="w-full text-xs bg-[#0b0c13] text-white border border-slate-800 focus:border-red-500 rounded-lg p-3 outline-none transition-colors font-mono"
                          />
                        </div>
                        <div>
                          <label className="text-xs font-bold text-slate-400 block mb-1">OpenAI API Key</label>
                          <input
                            type="text"
                            value={configSettings.openai_api_key || ""}
                            onChange={(e) => handleConfigChange("openai_api_key", e.target.value)}
                            placeholder="sk-..."
                            className="w-full text-xs bg-[#0b0c13] text-white border border-slate-800 focus:border-red-500 rounded-lg p-3 outline-none transition-colors font-mono"
                          />
                        </div>
                        <div>
                          <label className="text-xs font-bold text-slate-400 block mb-1">Groq API Key (optional)</label>
                          <input
                            type="text"
                            value={configSettings.groq_api_key || ""}
                            onChange={(e) => handleConfigChange("groq_api_key", e.target.value)}
                            placeholder="gsk_..."
                            className="w-full text-xs bg-[#0b0c13] text-white border border-slate-800 focus:border-red-500 rounded-lg p-3 outline-none transition-colors font-mono"
                          />
                        </div>
                        <div>
                          <label className="text-xs font-bold text-slate-400 block mb-1">Max Replies Per Run</label>
                          <input
                            type="number"
                            value={configSettings.max_replies_per_run ?? 50}
                            onChange={(e) => handleConfigChange("max_replies_per_run", parseInt(e.target.value) || 50)}
                            min={1} max={200}
                            className="w-full text-xs bg-[#0b0c13] text-white border border-slate-800 focus:border-red-500 rounded-lg p-3 outline-none transition-colors"
                          />
                        </div>
                        <div>
                          <label className="text-xs font-bold text-slate-400 block mb-1">Headless Browser</label>
                          <select
                            value={configSettings.headless ? "true" : "false"}
                            onChange={(e) => handleConfigChange("headless", e.target.value === "true")}
                            className="w-full text-xs bg-[#0b0c13] text-white border border-slate-800 focus:border-red-500 rounded-lg p-3 outline-none transition-colors cursor-pointer"
                          >
                            <option value="false">No (Show Browser)</option>
                            <option value="true">Yes (Headless)</option>
                          </select>
                        </div>
                      </div>
                      <div>
                        <label className="text-xs font-bold text-slate-400 block mb-1">Google Sheet Rules URL</label>
                        <input
                          type="text"
                          value={configSettings.google_sheet_rules_url || ""}
                          onChange={(e) => handleConfigChange("google_sheet_rules_url", e.target.value)}
                          placeholder="https://docs.google.com/spreadsheets/..."
                          className="w-full text-xs bg-[#0b0c13] text-white border border-slate-800 focus:border-red-500 rounded-lg p-3 outline-none transition-colors"
                        />
                        <p className="text-[10px] text-slate-500 mt-1">Public Google Sheet CSV with keyword→reply rules for YouTube Shorts.</p>
                      </div>
                    </div>
                  )}

                  {/* Action buttons - Start Bot Automation & Save Settings */}
                  <div className="flex justify-between items-center pt-2 gap-4 border-t border-slate-800/60 mt-4">
                    <div>
                      {status?.is_processing ? (
                        <button
                          type="button"
                          onClick={handleStopBot}
                          className="py-2.5 px-6 bg-rose-600 hover:bg-rose-700 text-white font-bold text-xs rounded-lg transition-all shadow-md shadow-rose-600/20 active:scale-95 cursor-pointer"
                        >
                          Stop Bot Automation
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={handleStartBot}
                          disabled={!isApiConnected || isStartingBot}
                          className="py-2.5 px-6 bg-emerald-600 hover:bg-emerald-700 disabled:bg-emerald-600/30 text-white font-bold text-xs rounded-lg transition-all shadow-md shadow-emerald-600/20 active:scale-95 cursor-pointer"
                        >
                          {isStartingBot ? "Starting Bot..." : "Start Bot Automation"}
                        </button>
                      )}
                    </div>

                    <div>
                      <button
                        type="submit"
                        disabled={isSavingConfig}
                        className="py-2.5 px-6 bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-600/35 text-white font-bold text-xs rounded-lg transition-all shadow-md shadow-indigo-600/20 active:scale-95 cursor-pointer"
                      >
                        {isSavingConfig ? "Saving Settings..." : "Save Settings"}
                      </button>
                    </div>
                  </div>
                </form>
              </div>

              {/* ROW 3: VISUAL CHART CARD */}
              <div className="bg-[#151824] border border-slate-800/80 rounded-2xl p-6 flex flex-col shadow-md">
                <h3 className="text-sm font-bold text-white uppercase tracking-wider mb-6">
                  Comment Activity ({
                    dateFilter === "today" ? "Today" :
                      dateFilter === "week" ? "Past 7 Days" :
                        dateFilter === "month" ? "Past 30 Days" : "All Time"
                  })
                </h3>

                <div className="min-h-[220px] relative">
                  <svg viewBox="0 0 500 220" className="w-full h-full">
                    <line x1="40" y1="20" x2="480" y2="20" stroke="#1f2937" strokeDasharray="3" />
                    <line x1="40" y1="70" x2="480" y2="70" stroke="#1f2937" strokeDasharray="3" />
                    <line x1="40" y1="120" x2="480" y2="120" stroke="#1f2937" strokeDasharray="3" />
                    <line x1="40" y1="170" x2="480" y2="170" stroke="#374151" />

                    <text x="30" y="24" fill="#64748b" fontSize="9" textAnchor="end">{Math.round(chartData.maxVal)}</text>
                    <text x="30" y="74" fill="#64748b" fontSize="9" textAnchor="end">{Math.round(chartData.maxVal / 2)}</text>
                    <text x="30" y="124" fill="#64748b" fontSize="9" textAnchor="end">0</text>

                    {chartData.values.map((val, idx) => {
                      const colWidth = 30;
                      const count = chartData.values.length;
                      const step = 400 / (count - 1);
                      const xCenter = 50 + idx * step;
                      const x = xCenter - (colWidth / 2);
                      const height = (val / chartData.maxVal) * 150;
                      const y = 170 - height;
                      return (
                        <g key={idx} className="group cursor-pointer">
                          <rect
                            x={x}
                            y={y}
                            width={colWidth}
                            height={height}
                            rx="4"
                            fill="#4f46e5"
                            className="transition-all duration-300 hover:fill-indigo-400"
                          />
                          <text
                            x={xCenter}
                            y={y - 8}
                            fill="#a5b4fc"
                            fontSize="10"
                            fontWeight="bold"
                            textAnchor="middle"
                            className="opacity-0 group-hover:opacity-100 transition-opacity duration-200"
                          >
                            {val}
                          </text>
                        </g>
                      );
                    })}

                    {chartData.labels.map((lbl, idx) => {
                      const count = chartData.labels.length;
                      const step = 400 / (count - 1);
                      const xCenter = 50 + idx * step;
                      return (
                        <text key={idx} x={xCenter} y="192" fill="#64748b" fontSize="9" textAnchor="middle">
                          {lbl}
                        </text>
                      );
                    })}
                  </svg>
                </div>
              </div>

              {/* ROW 4: TIMELINE OF COMMENT LOGS TABLE */}
              <div className="bg-[#151824] border border-slate-800/80 rounded-2xl p-6 shadow-md">

                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
                  <h3 className="text-sm font-bold text-white uppercase tracking-wider">Comment Activity History</h3>

                  <div className="flex flex-wrap items-center gap-3">
                    <input
                      type="text"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      placeholder="Search comments..."
                      className="text-xs bg-[#0b0c13] text-white border border-slate-800 focus:border-indigo-500 rounded-lg py-1.5 px-3 outline-none transition-colors w-40"
                    />

                    <select
                      value={postFilter}
                      onChange={(e) => setPostFilter(e.target.value)}
                      className="text-xs bg-[#0b0c13] text-white border border-slate-800 rounded-lg py-1.5 px-2.5 outline-none cursor-pointer max-w-[150px] truncate"
                    >
                      <option value="all">All Posts</option>
                      {filterOptions.posts.map((url, i) => (
                        <option key={i} value={url}>Post {i + 1} ({url.substring(0, 15)}...)</option>
                      ))}
                    </select>

                    {(accountFilter !== "all" || postFilter !== "all" || searchQuery.trim()) && (
                      <button
                        onClick={() => {
                          setAccountFilter("all");
                          setPostFilter("all");
                          setSearchQuery("");
                        }}
                        className="text-[10px] font-bold text-indigo-400 hover:text-indigo-300 underline cursor-pointer"
                      >
                        Reset Filters
                      </button>
                    )}
                  </div>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full text-left border-collapse">
                    <thead>
                      <tr className="border-b border-slate-800 text-[11px] font-bold text-slate-400 uppercase tracking-wider">
                        <th className="py-3 px-4">Date/Time</th>
                        <th className="py-3 px-4">Author</th>
                        <th className="py-3 px-4">Comment</th>
                        <th className="py-3 px-4">Generated Reply</th>
                        <th className="py-3 px-4">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/40 text-xs">
                      {filteredLogs.slice(0, 50).map((log, idx) => (
                        <tr
                          key={idx}
                          onClick={() => setSelectedLog(log)}
                          className="hover:bg-[#1f2336] transition-colors cursor-pointer group"
                        >
                          <td className="py-3 px-4 text-slate-400 whitespace-nowrap">
                            {log.timestamp ? new Date(log.timestamp).toLocaleString(undefined, {
                              month: "short",
                              day: "numeric",
                              hour: "2-digit",
                              minute: "2-digit",
                            }) : ""}
                          </td>
                          <td className="py-3 px-4 text-indigo-300 font-semibold group-hover:text-indigo-200">
                            {log.author}
                          </td>
                          <td className="py-3 px-4 text-slate-200 truncate max-w-[200px]">
                            {log.comment}
                          </td>
                          <td className="py-3 px-4 text-slate-400 truncate max-w-[250px]">
                            {log.reply || "-"}
                          </td>
                          <td className="py-3 px-4 whitespace-nowrap">
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${log.status === "replied"
                                  ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                                  : log.status === "skipped"
                                    ? "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                                    : "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                                }`}
                            >
                              {log.status}
                            </span>
                          </td>
                        </tr>
                      ))}

                      {filteredLogs.length === 0 && (
                        <tr>
                          <td colSpan={5} className="py-8 text-center text-slate-500">
                            No logs found matching selected filters.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>

                <div className="mt-4 text-[10px] text-slate-500 flex justify-between items-center">
                  <span>Showing top 50 matches</span>
                  <span>Total filtered: {filteredLogs.length}</span>
                </div>

              </div>

            </div>
          )}

          {/* TAB 2: LIVE LOGS */}
          {activeTab === "logs" && (
            <div className="max-w-6xl mx-auto flex flex-col h-[calc(100vh-12rem)]">

              <div className="flex justify-between items-center mb-3">
                <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">Live System Logs tail</span>

                <div className="flex gap-2">
                  <button
                    onClick={fetchData}
                    className="py-1 px-3 bg-slate-800 hover:bg-slate-700 text-slate-300 font-bold text-[10px] rounded-lg transition-colors border border-slate-700 cursor-pointer"
                  >
                    Refresh
                  </button>
                  <button
                    onClick={handleClearLogs}
                    disabled={isClearingLogs}
                    className="py-1 px-3 bg-red-600/20 hover:bg-red-600/40 text-red-400 font-bold text-[10px] rounded-lg transition-colors border border-red-500/20 cursor-pointer"
                  >
                    {isClearingLogs ? "Clearing..." : "Clear System Logs"}
                  </button>
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(appLogs);
                      showToast("Logs copied to clipboard!", "success");
                    }}
                    className="py-1 px-3 bg-indigo-600/20 hover:bg-indigo-600/35 text-indigo-400 font-bold text-[10px] rounded-lg transition-colors border border-indigo-500/20 cursor-pointer"
                  >
                    Copy Logs
                  </button>
                </div>
              </div>

              <div className="flex-1 bg-[#07080d] rounded-2xl p-6 border border-slate-800/80 font-mono text-[11px] overflow-y-auto leading-relaxed shadow-lg flex flex-col gap-1 select-text">
                <div className="whitespace-pre-wrap text-slate-300">
                  {appLogs || "No logs available. Start bot monitoring to generate logs."}
                </div>
                <div ref={terminalEndRef}></div>
              </div>
            </div>
          )}

        </div>

      </main>

      {/* DETAIL MODAL DIALOG POPUP */}
      {selectedLog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-2xl bg-[#161925] border border-slate-800 rounded-2xl shadow-xl overflow-hidden flex flex-col">

            <div className="px-6 py-4 border-b border-slate-800 flex justify-between items-center bg-[#1b1f2e]">
              <div>
                <h3 className="font-bold text-white text-sm">Interaction Detail</h3>
                <span className="text-[10px] text-slate-500 mt-1 block">Comment ID: {selectedLog.comment_id}</span>
              </div>
              <button
                onClick={() => setSelectedLog(null)}
                className="text-slate-400 hover:text-white cursor-pointer"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="p-6 space-y-4 overflow-y-auto max-h-[70vh] text-xs">

              <div className="grid grid-cols-3 gap-4">
                <div>
                  <span className="text-[10px] font-bold text-slate-500 uppercase block mb-1">Status</span>
                  <span
                    className={`px-2.5 py-0.5 rounded text-[10px] font-bold uppercase inline-block ${selectedLog.status === "replied"
                        ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                        : selectedLog.status === "skipped"
                          ? "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                          : "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                      }`}
                  >
                    {selectedLog.status}
                  </span>
                </div>
                <div>
                  <span className="text-[10px] font-bold text-slate-500 uppercase block mb-1">Author</span>
                  <span className="text-white font-semibold">{selectedLog.author}</span>
                </div>
                <div>
                  <span className="text-[10px] font-bold text-slate-500 uppercase block mb-1">Time Elapsed</span>
                  <span className="text-slate-300">{selectedLog.processing_time || "0.00s"}</span>
                </div>
              </div>

              <div>
                <span className="text-[10px] font-bold text-slate-500 uppercase block mb-1">Timestamp</span>
                <span className="text-slate-300">{selectedLog.timestamp ? new Date(selectedLog.timestamp).toLocaleString() : "-"}</span>
              </div>

              <div>
                <span className="text-[10px] font-bold text-slate-500 uppercase block mb-1">Post URL</span>
                <a
                  href={selectedLog.post_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-indigo-400 hover:underline break-all"
                >
                  {selectedLog.post_url}
                </a>
              </div>

              <div className="space-y-3 pt-2">
                <div className="p-4 bg-[#0c0d14] rounded-xl border border-slate-800/80">
                  <span className="text-[10px] font-bold text-indigo-400 uppercase block mb-2">Original Comment</span>
                  <p className="text-slate-200 leading-relaxed italic">"{selectedLog.comment}"</p>
                </div>

                <div className="p-4 bg-[#0c0d14] rounded-xl border border-slate-800/80">
                  <span className="text-[10px] font-bold text-emerald-400 uppercase block mb-2">Generated Reply</span>
                  <p className="text-slate-200 leading-relaxed">
                    {selectedLog.reply || <span className="text-slate-500 italic">No reply generated (Skipped/Reason: {selectedLog.reason || "-"})</span>}
                  </p>
                </div>
              </div>

            </div>

            <div className="px-6 py-4 border-t border-slate-800 bg-[#1b1f2e] flex justify-end gap-3">
              <a
                href={selectedLog.post_url}
                target="_blank"
                rel="noreferrer"
                className="py-2 px-4 bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-xs rounded-lg transition-colors flex items-center gap-1.5"
              >
                Open Post Link
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                </svg>
              </a>
              <button
                onClick={() => setSelectedLog(null)}
                className="py-2 px-4 bg-slate-800 hover:bg-slate-700 text-slate-300 font-bold text-xs rounded-lg transition-colors cursor-pointer"
              >
                Close
              </button>
            </div>

          </div>
        </div>
      )}

      {/* PREMIUM CUSTOM CONFIRMATION MODAL OVERLAY */}
      {confirmModal.isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-md animate-fade-in">
          <div className="w-full max-w-md bg-[#161925] border border-slate-800/80 rounded-2xl shadow-2xl overflow-hidden flex flex-col p-6 animate-scale-up">

            {/* Title Header with warning icon */}
            <div className="flex items-center gap-3 mb-4">
              <div className="p-2.5 bg-rose-500/10 rounded-xl text-rose-400">
                <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              </div>
              <h3 className="font-bold text-white text-base">{confirmModal.title}</h3>
            </div>

            {/* Message */}
            <p className="text-xs text-slate-300 leading-relaxed mb-6">
              {confirmModal.message}
            </p>

            {/* Action buttons */}
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setConfirmModal((prev) => ({ ...prev, isOpen: false }))}
                className="py-2 px-4 bg-slate-800 hover:bg-slate-700 text-slate-300 font-bold text-xs rounded-lg transition-colors cursor-pointer active:scale-95"
              >
                Cancel
              </button>
              <button
                onClick={confirmModal.onConfirm}
                className="py-2 px-5 bg-rose-600 hover:bg-rose-700 text-white font-bold text-xs rounded-lg shadow-md shadow-rose-600/10 transition-colors cursor-pointer active:scale-95"
              >
                Permanently Clear
              </button>
            </div>

          </div>
        </div>
      )}

      {/* FLOATING TOAST NOTIFICATION CORNER */}
      <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-2.5 max-w-sm pointer-events-none">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`p-4 rounded-xl border text-xs font-semibold shadow-xl flex items-center gap-3 pointer-events-auto animate-slide-in-right transition-all ${toast.type === "success"
                ? "bg-emerald-950/80 text-emerald-400 border-emerald-500/30 backdrop-blur-md"
                : toast.type === "error"
                  ? "bg-rose-955/80 text-rose-400 border-rose-500/30 backdrop-blur-md"
                  : "bg-indigo-950/80 text-indigo-400 border-indigo-500/30 backdrop-blur-md"
              }`}
          >
            {toast.type === "success" && (
              <div className="p-1 rounded bg-emerald-500/10 text-emerald-400">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              </div>
            )}
            {toast.type === "error" && (
              <div className="p-1 rounded bg-rose-500/10 text-rose-400">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              </div>
            )}
            {toast.type === "info" && (
              <div className="p-1 rounded bg-indigo-500/10 text-indigo-400">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
            )}
            <span className="flex-1 leading-snug">{toast.message}</span>
          </div>
        ))}
      </div>

    </div>
  );
}
