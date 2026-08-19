import { useEffect, useState } from "react";
import RunPage from "./pages/Run";
import ConfigPage from "./pages/Config";
import HistoryPage from "./pages/History";
import UpdateBanner from "./components/UpdateBanner";
import { useRunStore, useT } from "./store";
import { ipc } from "./lib/ipc";
import { isLang } from "./lib/i18n";

type Tab = "run" | "config" | "history";

// 顶部标题 + tab 区域
// 设计：editorial 风格 —— 大号衬线标题 + 字距夸张的小 caps tab
// 当前 tab 用底部黑色粗线表示，而不是 pill 反色块
export default function App() {
  const [tab, setTab] = useState<Tab>("run");
  const [helpMsg, setHelpMsg] = useState<string | null>(null);
  const running = useRunStore((s) => s.running);
  const setLang = useRunStore((s) => s.setLang);
  const t = useT();

  // 「问 AI 帮忙」：把 app 上下文 + 当前实时日志缓冲打成一段求助文本复制到剪贴板，
  // 用户粘到任意聊天 AI 即可。常驻 header，每个 tab 都点得到（不只出错时）。
  async function askAi() {
    try {
      const logs = useRunStore.getState().logs;
      const { text } = await ipc.getAiHelpReport(logs);
      await navigator.clipboard.writeText(text);
      setHelpMsg(t("askai.ok"));
    } catch {
      setHelpMsg(t("askai.fail"));
    }
    setTimeout(() => setHelpMsg(null), 6000);
  }

  // 启动时读回 .env 里存的 UI 语言，覆盖系统探测的默认。
  // 没设过（首次启动）则把探测到的默认落进 .env，让后端报错（读 BOSS_LANG）跟
  // 前端展示的语言一致——否则非中文用户首次见到的后端串会是中文。读/写失败都
  // 静默：大不了下次启动再探测一次，不打断使用。
  useEffect(() => {
    ipc.getLanguage()
      .then(({ lang }) => {
        if (isLang(lang)) {
          setLang(lang);
        } else {
          const detected = useRunStore.getState().lang;
          ipc.setLanguage(detected).catch(() => {});
        }
      })
      .catch(() => {});
  }, [setLang]);

  return (
    <div className="min-h-screen flex flex-col">
      <UpdateBanner />
      <header className="border-b border-[var(--border-light)] bg-white shadow-sm">
        <div className="max-w-7xl mx-auto px-6 md:px-8 py-4 flex items-center gap-8">
          <h1 className="text-xl md:text-2xl font-semibold leading-none tracking-tight whitespace-nowrap">
            Boss<span className="font-normal text-[var(--accent)]">·</span>Zhipin
            <span className="block text-xs font-normal text-[var(--muted-fg)] mt-1">
              {t("header.subtitle")}
            </span>
          </h1>

          {/* tab 群：uppercase mono，当前项底部 4px 黑线 */}
          <nav className="flex gap-1 rounded-lg bg-[var(--muted)] p-1">
            <TabButton current={tab} value="run" onClick={setTab}>
              <span className="inline-flex items-center gap-2">
                {t("tab.run")}
                {running && (
                  // 运行中标记：闪烁的黑色实心方块（不是绿色圆点）
                  <span className="inline-block w-2 h-2 rounded-full bg-[var(--accent)] animate-pulse" />
                )}
              </span>
            </TabButton>
            <TabButton current={tab} value="config" onClick={setTab}>
              {t("tab.config")}
            </TabButton>
            <TabButton current={tab} value="history" onClick={setTab}>
              {t("tab.history")}
            </TabButton>
          </nav>

          {/* 右侧：常驻「问 AI」入口 + Chrome 提示语（复制成功时提示语临时换成反馈） */}
          <div className="ml-auto flex flex-col items-end gap-1.5 max-w-[280px]">
            <button
              onClick={askAi}
              className="btn-outline px-3 py-1.5 text-xs"
            >
              {t("header.askAi")}
            </button>
            {helpMsg ? (
              <span className="text-[10px] font-mono text-right text-[var(--ink)] leading-relaxed">
                {helpMsg}
              </span>
            ) : (
              <span className="text-[10px] font-mono uppercase tracking-widest text-right text-[var(--muted-fg)] leading-relaxed">
                {t("header.warn1")}
                <br />
                {t("header.warn2")}
              </span>
            )}
          </div>
        </div>
      </header>

      <main className="flex-1 overflow-auto">
        <div className="max-w-7xl mx-auto px-6 md:px-8 py-8">
          {tab === "run" && <RunPage />}
          {tab === "config" && <ConfigPage />}
          {tab === "history" && <HistoryPage />}
        </div>
      </main>
    </div>
  );
}

function TabButton({
  current,
  value,
  onClick,
  children,
}: {
  current: Tab;
  value: Tab;
  onClick: (t: Tab) => void;
  children: React.ReactNode;
}) {
  const active = current === value;
  return (
    <button
      onClick={() => onClick(value)}
      className={[
        "rounded-md px-4 py-2 text-sm font-medium transition-colors duration-150",
        active
          ? "bg-white text-[var(--accent)] shadow-sm"
          : "text-[var(--muted-fg)] hover:text-[var(--ink)]",
      ].join(" ")}
    >
      {children}
    </button>
  );
}
