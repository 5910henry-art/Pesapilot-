import { useCallback, useEffect, useState } from "react";
import {
  BrainCircuit,
  RefreshCw,
  AlertTriangle,
  CheckCircle2,
  TrendingUp,
  TrendingDown,
  Wallet,
  PiggyBank,
  Receipt,
  HandCoins,
  Lightbulb,
  ShieldCheck,
  Send,
  Sparkles,
} from "lucide-react";
import { useAuth } from "../context/useAuth";
import { aiApi } from "../api/client";

function formatMoney(minor, currency = "KES", minorUnit) {
  const amount = Number(minor || 0) / (10 ** (
    Number.isInteger(Number(minorUnit))
      ? Math.min(4, Math.max(0, Number(minorUnit)))
      : 2
  ));

  if (!Number.isFinite(amount)) return "—";

  try {
    return new Intl.NumberFormat("en-KE", {
      style: "currency",
      currency,
    }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(2)}`;
  }
}

function formatInsightText(value, item, currency, minorUnit) {
  const insightCurrency = item.currency || currency;
  const insightMinorUnit =
    insightCurrency === currency ? minorUnit : undefined;

  return String(value || "").replace(
    /(\\d[\\d,]*)\\s+minor units/gi,
    (_, rawAmount) => formatMoney(
      Number(rawAmount.replace(/,/g, "")),
      insightCurrency,
      insightMinorUnit
    )
  );
}

function renderAnswerMarkdown(value) {
  const renderInline = (text, keyPrefix) => {
    const parts = String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/g);

    return parts.map((part, index) => {
      const key = `${keyPrefix}-${index}`;

      if (part.startsWith("**") && part.endsWith("**")) {
        return <strong key={key}>{part.slice(2, -2)}</strong>;
      }

      if (part.startsWith("`") && part.endsWith("`")) {
        return <code key={key}>{part.slice(1, -1)}</code>;
      }

      return part;
    });
  };

  const lines = String(value || "")
    .replace(/\\\*\*/g, "**")
    .replace(/\\\*/g, "*")
    .split(/\r?\n/);

  const blocks = [];
  let paragraph = [];
  let list = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push(
        <p key={`p-${blocks.length}`}>
          {renderInline(paragraph.join(" "), `p-${blocks.length}`)}
        </p>
      );
      paragraph = [];
    }
  };

  const flushList = () => {
    if (list.length) {
      blocks.push(
        <ul key={`ul-${blocks.length}`}>
          {list.map((item, index) => (
            <li key={index}>
              {renderInline(item, `li-${blocks.length}-${index}`)}
            </li>
          ))}
        </ul>
      );
      list = [];
    }
  };

  lines.forEach((line) => {
    const trimmed = line.trim();

    if (!trimmed) {
      flushParagraph();
      flushList();
      return;
    }

    const item = trimmed.match(/^(?:[-*]|\d+\.)\s+(.+)$/);

    if (item) {
      flushParagraph();
      list.push(item[1]);
      return;
    }

    flushList();
    paragraph.push(trimmed);
  });

  flushParagraph();
  flushList();

  return blocks;
}

function formatDate(value) {
  if (!value) return "No target date";

  const date = new Date(`${value}T12:00:00`);

  if (Number.isNaN(date.getTime())) return value;

  return new Intl.DateTimeFormat("en-KE", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(date);
}

function answerQuestion(question, snapshot) {
  const text = question.toLowerCase();
  const summary = snapshot.summary || {};
  const currency = snapshot.currency || "KES";
  const minorUnit = snapshot.minor_unit ?? 2;
  const money = (value, code = currency) =>
    formatMoney(value, code, code === currency ? minorUnit : undefined);

  const income = Number(summary.monthly_income_minor || 0);
  const expenses = Number(summary.monthly_expenses_minor || 0);
  const net = Number(summary.monthly_net_minor || income - expenses);

  if (
    /\b(balance|how much money|total assets|available money|net worth)\b/.test(text)
  ) {
    return {
      title: "Your financial position",
      answer: `Your recorded total assets are ${money(summary.total_assets_minor)}. Your recorded liabilities are ${money(summary.total_liabilities_minor)}, giving you a net worth of ${money(summary.net_worth_minor)}.`,
    };
  }

  if (/\b(income|earned|earning|salary|received)\b/.test(text)) {
    return {
      title: "Your income this month",
      answer: `Your recorded income for this month is ${money(income)}.`,
    };
  }

  if (/\b(expense|expenses|spending|spent|spend)\b/.test(text)) {
    return {
      title: "Your spending this month",
      answer: `Your recorded expenses for this month are ${money(expenses)}. ${
        income > 0
          ? `That is ${Math.round((expenses / income) * 100)}% of your recorded income.`
          : "There is no recorded income this month to compare against."
      }`,
    };
  }

  if (/\b(cash flow|cashflow|left over|surplus|deficit)\b/.test(text)) {
    return {
      title: "Your monthly cash flow",
      answer: `Your recorded income is ${money(income)} and expenses are ${money(expenses)}. Your net cash flow is ${money(net)}. ${
        net < 0
          ? "Your recorded expenses currently exceed your income."
          : net > 0
            ? "Your recorded income currently exceeds your expenses."
            : "Your recorded income and expenses are currently equal."
      }`,
    };
  }

  if (/\b(budget|budgets|overspend|overspending)\b/.test(text)) {
    const budgets = snapshot.budgets || [];

    if (!budgets.length) {
      return {
        title: "Your budgets",
        answer: "There are no active budgets in the current financial snapshot.",
      };
    }

    const details = budgets.flatMap((budget) => {
      const allocations = Array.isArray(budget.categories) && budget.categories.length
        ? budget.categories
        : [{
            category: budget.category || "Uncategorized",
            amount_minor: budget.amount_minor,
            actual_minor: budget.actual_minor,
            remaining_minor: budget.remaining_minor,
            usage_percentage: budget.usage_percentage,
            is_over_budget: budget.is_over_budget,
          }];

      return allocations.map((allocation) => {
        const usage = Number(allocation.usage_percentage || 0);
        const status = allocation.is_over_budget ? "over budget" : "within budget";
        return `${allocation.category || "Uncategorized"}: ${money(allocation.actual_minor, budget.currency)} spent of ${money(allocation.amount_minor, budget.currency)}, ${usage.toFixed(1)}% used, ${money(allocation.remaining_minor, budget.currency)} remaining (${status})`;
      });
    });

    return {
      title: "Your budget position",
      answer: details.join(". ") + ".",
    };
  }

  if (/\b(bill|bills|due|overdue|payable)\b/.test(text)) {
    const bills = snapshot.bills || [];
    const outstanding = bills.filter((bill) => Number(bill.remaining_minor) > 0);
    const overdue = outstanding.filter((bill) => bill.status === "overdue");
    const total = outstanding.reduce(
      (sum, bill) => sum + Number(bill.remaining_minor || 0),
      0
    );

    return {
      title: "Your outstanding bills",
      answer: `You have ${outstanding.length} outstanding bill(s), totalling ${money(total)} in their recorded currencies where applicable. ${overdue.length} of these are marked overdue.${
        overdue.length
          ? ` Overdue bills include: ${overdue.map((bill) => bill.title).join(", ")}.`
          : ""
      }`,
    };
  }

  if (/\b(saving|savings|goal|goals)\b/.test(text)) {
    const goals = snapshot.savings_goals || [];

    if (!goals.length) {
      return {
        title: "Your savings goals",
        answer: "There are no active or paused savings goals in the current snapshot.",
      };
    }

    return {
      title: "Your savings progress",
      answer: goals.map((goal) =>
        `${goal.name}: ${money(goal.saved_minor, goal.currency)} saved out of ${money(goal.target_minor, goal.currency)} (${Number(goal.progress_percentage || 0).toFixed(1)}% complete)`
      ).join(". ") + ".",
    };
  }

  if (/\b(loan|loans|debt|borrow|borrowed|lent|repayment)\b/.test(text)) {
    const loans = snapshot.loans || [];
    const borrowed = loans.filter((loan) => loan.direction === "borrowed");
    const lent = loans.filter((loan) => loan.direction === "lent");
    const overdue = loans.reduce(
      (sum, loan) => sum + Number(loan.overdue_installments_minor || 0),
      0
    );

    return {
      title: "Your loan position",
      answer: `You have ${borrowed.length} active recorded loan(s) borrowed and ${lent.length} active recorded loan(s) lent. Recorded overdue installments total ${money(overdue)} in the workspace currency calculation.`,
    };
  }

  return {
    title: "I couldn't identify that question",
    answer: "I can currently answer questions about your total assets and net worth, monthly income, expenses, cash flow, budgets, outstanding bills, savings goals and loans. Try asking about one of those areas.",
  };
}

function StatCard({ icon: Icon, label, value, note, tone }) {
  return (
    <article className="ai-stat-card">
      <div className={`ai-stat-icon ${tone}`}>
        <Icon size={19} />
      </div>
      <div className="ai-stat-copy">
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{note}</small>
      </div>
    </article>
  );
}

export default function AIManager() {
  const { activeWorkspace } = useAuth();
  const workspaceId = activeWorkspace?.id;

  const [overview, setOverview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState(null);
  const [externalAiConsent, setExternalAiConsent] = useState(false);
  const [asking, setAsking] = useState(false);
  const [questionError, setQuestionError] = useState("");

  const loadOverview = useCallback(async (refresh = false) => {
    if (!workspaceId) {
      setOverview(null);
      setLoading(false);
      setRefreshing(false);
      return;
    }

    refresh ? setRefreshing(true) : setLoading(true);
    setError("");

    try {
      const result = await aiApi.getOverview(workspaceId);

      if (result?.success === false) {
        throw new Error(result.message || "Unable to load AI Manager.");
      }

      setOverview(result.data);
    } catch (err) {
      setError(err.message || "Unable to load your financial insights.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    loadOverview();
  }, [loadOverview]);

  const snapshot = overview?.snapshot;
  const summary = snapshot?.summary || {};
  const currency = snapshot?.currency || activeWorkspace?.currency || "KES";
  const minorUnit = snapshot?.minor_unit ?? 2;
  const money = (value) => formatMoney(value, currency, minorUnit);

  const insights = overview?.insights || [];
  const highCount = insights.filter((item) => item.severity === "high").length;
  const mediumCount = insights.filter((item) => item.severity === "medium").length;

  async function submitQuestion(event) {
    event.preventDefault();

    const trimmedQuestion = question.trim();

    if (!trimmedQuestion || !snapshot || asking) return;

    setQuestionError("");
    setAnswer(null);

    if (!externalAiConsent) {
      setAnswer(answerQuestion(trimmedQuestion, snapshot));
      return;
    }

    setAsking(true);

    try {
      const result = await aiApi.ask(workspaceId, trimmedQuestion, externalAiConsent);

      if (!result?.success || !result?.data?.answer) {
        throw new Error(
          result?.message || "The external AI could not answer your question."
        );
      }

      setAnswer({
        title: "Your financial answer",
        answer: result.data.answer,
      });
    } catch (err) {
      setQuestionError(
        err.message || "Unable to get an answer from the external AI."
      );
    } finally {
      setAsking(false);
    }
  }

  return (
    <div className="ai-manager-page">
      <section className="ai-manager-hero">
        <div className="ai-hero-copy">
          <div className="ai-eyebrow">
            <Sparkles size={14} />
            YOUR PERSONAL FINANCIAL INTELLIGENCE
          </div>
          <h1>AI Manager</h1>
          <p>
            Understand your money, identify risks and make more informed
            financial decisions.
          </p>
          <div className="ai-readonly-note">
            <ShieldCheck size={15} />
            <span>Read-only insights. AI Manager cannot change records or make financial transactions.</span>
          </div>
        </div>
        <div className="ai-hero-symbol">
          <BrainCircuit size={48} strokeWidth={1.4} />
        </div>
        <button
          type="button"
          className="ai-refresh-button"
          onClick={() => loadOverview(true)}
          disabled={loading || refreshing || !workspaceId}
          aria-label="Refresh financial insights"
        >
          <RefreshCw size={17} className={refreshing ? "ai-spinning" : ""} />
          {refreshing ? "Refreshing" : "Refresh"}
        </button>
      </section>

      {!workspaceId && (
        <div className="ai-message ai-error">
          Select a workspace to view your financial intelligence.
        </div>
      )}

      {error && (
        <div className="ai-message ai-error" role="alert">
          <AlertTriangle size={17} />
          {error}
          <button type="button" onClick={() => loadOverview(true)}>
            Try again
          </button>
        </div>
      )}

      {loading && (
        <div className="ai-loading-grid" aria-label="Loading AI Manager">
          {Array.from({ length: 4 }).map((_, index) => (
            <div className="ai-loading-card" key={index} />
          ))}
          <div className="ai-loading-panel" />
          <div className="ai-loading-panel" />
        </div>
      )}

      {!loading && snapshot && (
        <>
          <section className="ai-stats-grid">
            <StatCard
              icon={Wallet}
              label="Total assets"
              value={money(summary.total_assets_minor)}
              note="Across your asset accounts"
              tone="green"
            />
            <StatCard
              icon={TrendingUp}
              label="Income this month"
              value={money(summary.monthly_income_minor)}
              note="Recorded income"
              tone="blue"
            />
            <StatCard
              icon={TrendingDown}
              label="Expenses this month"
              value={money(summary.monthly_expenses_minor)}
              note="Recorded expenses"
              tone="orange"
            />
            <StatCard
              icon={PiggyBank}
              label="Net worth"
              value={money(summary.net_worth_minor)}
              note="Assets less liabilities"
              tone="purple"
            />
          </section>

          <section className="ai-health-panel">
            <div className="ai-section-heading">
              <div>
                <h2>Financial health signals</h2>
                <p>Based on your recorded financial activity this month</p>
              </div>
              <span className="ai-live-label">
                <span /> Snapshot
              </span>
            </div>

            <div className="ai-cashflow-row">
              <div>
                <span>Monthly net cash flow</span>
                <strong className={
                  Number(summary.monthly_net_minor || 0) < 0
                    ? "ai-negative"
                    : "ai-positive"
                }>
                  {money(summary.monthly_net_minor)}
                </strong>
              </div>
              <div className="ai-cashflow-breakdown">
                <span>Income: {money(summary.monthly_income_minor)}</span>
                <span>Expenses: {money(summary.monthly_expenses_minor)}</span>
              </div>
            </div>

            <div className="ai-alert-counts">
              <div>
                <AlertTriangle size={16} />
                <strong>{highCount}</strong>
                <span>High priority</span>
              </div>
              <div>
                <Lightbulb size={16} />
                <strong>{mediumCount}</strong>
                <span>Needs attention</span>
              </div>
              <div>
                <CheckCircle2 size={16} />
                <strong>{insights.length}</strong>
                <span>Total insights</span>
              </div>
            </div>
          </section>

          <section className="ai-intelligence-grid">
            <article className="ai-intelligence-panel">
              <div className="ai-section-heading">
                <div>
                  <h2>Income &amp; spending trends</h2>
                  <p>Your monthly income and expenses over the last six months</p>
                </div>
              </div>

              {Array.isArray(snapshot.monthly_trends) &&
              snapshot.monthly_trends.length ? (
                <>
                  <div className="ai-chart-legend">
                    <span><i className="income" /> Income</span>
                    <span><i className="expenses" /> Expenses</span>
                  </div>

                  <div className="ai-trend-chart">
                    {(() => {
                      const trends = snapshot.monthly_trends;
                      const maxValue = Math.max(
                        1,
                        ...trends.flatMap((item) => [
                          Number(item.income_minor || 0),
                          Number(item.expenses_minor || 0),
                        ])
                      );

                      return trends.map((item) => {
                        const income = Math.max(0, Number(item.income_minor || 0));
                        const expenses = Math.max(0, Number(item.expenses_minor || 0));
                        const monthLabel = new Date(
                          `${item.month}-01T12:00:00`
                        ).toLocaleDateString(undefined, { month: "short" });

                        return (
                          <div className="ai-trend-month" key={item.month}>
                            <div className="ai-trend-bars">
                              <div
                                className="ai-trend-bar income"
                                style={{ height: `${income / maxValue * 100}%` }}
                                title={`Income: ${money(income)}`}
                              />
                              <div
                                className="ai-trend-bar expenses"
                                style={{ height: `${expenses / maxValue * 100}%` }}
                                title={`Expenses: ${money(expenses)}`}
                              />
                            </div>
                            <span className="ai-trend-month-label">{monthLabel}</span>
                            <strong className={
                              Number(item.net_minor || 0) < 0
                                ? "ai-negative"
                                : "ai-positive"
                            }>
                              {money(item.net_minor)}
                            </strong>
                            <small>Net</small>
                          </div>
                        );
                      });
                    })()}
                  </div>
                </>
              ) : (
                <p className="ai-no-data">No monthly trend data available yet.</p>
              )}
            </article>

            <article className="ai-intelligence-panel">
              <div className="ai-section-heading">
                <div>
                  <h2>Where your money goes</h2>
                  <p>Your five highest spending categories this month</p>
                </div>
              </div>

              {(snapshot.top_spending_categories || []).length ? (
                <div className="ai-category-list">
                  {snapshot.top_spending_categories.map((item, index) => {
                    const amount = Number(item.amount_minor || 0);
                    const total = Number(summary.monthly_expenses_minor || 0);
                    const percentage = total > 0
                      ? Math.max(0, amount / total * 100)
                      : 0;

                    return (
                      <div className="ai-category-item" key={`${item.category}-${index}`}>
                        <div className="ai-category-label">
                          <strong>{item.category}</strong>
                          <span>{money(amount)}</span>
                        </div>
                        <div className="ai-progress-track">
                          <div
                            className="ai-progress-fill"
                            style={{ width: `${Math.min(100, percentage)}%` }}
                          />
                        </div>
                        <small>{percentage.toFixed(1)}% of monthly expenses</small>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="ai-no-data">No categorized spending recorded this month.</p>
              )}
            </article>
          </section>

          <section className="ai-insights-section">
            <div className="ai-section-heading">
              <div>
                <h2>Insights and recommendations</h2>
                <p>Prioritized findings from your financial records</p>
              </div>
              <span className="ai-count-pill">{insights.length} findings</span>
            </div>

            {insights.length ? (
              <div className="ai-insights-list">
                {insights.map((item, index) => (
                  <article
                    className={`ai-insight-card severity-${item.severity}`}
                    key={`${item.rule}-${item.entity_id || index}`}
                  >
                    <div className="ai-insight-symbol">
                      {item.severity === "high"
                        ? <AlertTriangle size={19} />
                        : <Lightbulb size={19} />}
                    </div>
                    <div className="ai-insight-content">
                      <div className="ai-insight-topline">
                        <h3>{item.title}</h3>
                        <span className={`ai-severity ${item.severity}`}>
                          {item.severity}
                        </span>
                      </div>
                      <p>{formatInsightText(item.message, item, currency, minorUnit)}</p>
                      <div className="ai-recommendation">
                        <strong>Recommendation</strong>
                        <span>{formatInsightText(item.recommendation, item, currency, minorUnit)}</span>
                      </div>
                      {item.amount_minor != null && (
                        <small className="ai-insight-amount">
                          Amount involved: {formatMoney(
                            item.amount_minor,
                            item.currency || currency,
                            (item.currency || currency) === currency
                              ? minorUnit
                              : undefined
                          )}
                        </small>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className="ai-empty-state">
                <CheckCircle2 size={28} />
                <strong>No priority issues identified</strong>
                <span>
                  The current rules have not detected any items requiring
                  attention in the available records.
                </span>
              </div>
            )}
          </section>

          <section className="ai-question-panel">
            <div className="ai-question-heading">
              <div className="ai-question-icon">
                <BrainCircuit size={22} />
              </div>
              <div>
                <h2>Ask about your finances</h2>
                <p>
                  Ask a question about the financial records in this workspace.
                </p>
              </div>
            </div>

            <form className="ai-question-form" onSubmit={submitQuestion}>
              <input
                type="text"
                value={question}
                onChange={(event) => {
                  setQuestion(event.target.value);
                  setQuestionError("");
                }}
                placeholder="e.g. How much did I spend this month?"
                aria-label="Ask a financial question"
                disabled={asking}
                maxLength={2000}
              />
              <button
                type="submit"
                disabled={!question.trim() || asking || !snapshot}
              >
                <Send size={16} />
                {asking ? "Thinking..." : "Ask"}
              </button>
            </form>

            <label className="ai-external-consent">
              <input
                type="checkbox"
                checked={externalAiConsent}
                onChange={(event) => {
                  setExternalAiConsent(event.target.checked);
                  setQuestionError("");
                  setAnswer(null);
                }}
                disabled={asking}
              />
              <span>
                <strong>Allow my question and a limited financial summary to be shared with an external AI service for this answer.</strong>
              </span>
            </label>

            {questionError && (
              <div className="ai-message ai-error" role="alert">
                <AlertTriangle size={16} />
                {questionError}
              </div>
            )}

            <div className="ai-question-suggestions">
              {[
                "How much income did I receive this month?",
                "Which budgets are close to their limits?",
                "Do I have overdue bills?",
                "How are my savings goals progressing?",
              ].map((suggestion) => (
                <button
                  type="button"
                  key={suggestion}
                  onClick={() => {
                    setQuestion(suggestion);
                    setAnswer(null);
                    setQuestionError("");
                  }}
                  disabled={asking}
                >
                  {suggestion}
                </button>
              ))}
            </div>

            {answer && (
              <div className="ai-answer-card" aria-live="polite">
                <strong>{answer.title}</strong>
                <div className="ai-answer-content">{renderAnswerMarkdown(answer.answer)}</div>
              </div>
            )}

            <div className="ai-engine-note">
              <ShieldCheck size={14} />
              <span>
                {externalAiConsent
                  ? "External AI is enabled for your next question. Your question and a limited financial summary will be shared with an external AI service."
                  : "Local mode is active. Your question is answered using this workspace snapshot without sharing it with an external AI service."}
              </span>
            </div>
          </section>
        </>
      )}

    </div>
  );
}

function ChartNoAxesCombinedIcon() {
  return <TrendingUp size={20} />;
}
