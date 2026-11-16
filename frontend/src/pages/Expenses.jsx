import {
  ArrowDownRight,
  Info,
  ReceiptText,
  ShieldCheck,
  Sparkles,
  WalletCards,
} from "lucide-react";
import TransactionEntryForm from "../components/TransactionEntryForm";

export default function Expenses() {
  return (
    <div className="expenses-page">
      <section className="expenses-hero">
        <div className="expenses-hero-copy">
          <div className="expenses-kicker">
            <Sparkles size={14} /> SPENDING OVERVIEW
          </div>
          <h1>Expenses</h1>
          <p>
            Keep track of where your money goes. Record everyday purchases
            and payments from the accounts you use.
          </p>
          <div className="expenses-hero-meta">
            <span><ArrowDownRight size={15} /> Money going out</span>
            <span><WalletCards size={15} /> Linked to your accounts</span>
          </div>
        </div>
        <div className="expenses-hero-art" aria-hidden="true">
          <div className="expenses-art-orbit expenses-art-orbit-one" />
          <div className="expenses-art-orbit expenses-art-orbit-two" />
          <div className="expenses-art-icon">
            <ReceiptText size={35} strokeWidth={1.6} />
          </div>
        </div>
      </section>

      <section className="expenses-record-section">
        <div className="expenses-section-heading">
          <div>
            <span className="expenses-section-kicker">NEW RECORD</span>
            <h2>Record an expense</h2>
            <p>Add the details below to update your spending history.</p>
          </div>
          <div className="expenses-private-note">
            <ShieldCheck size={17} />
            <span>Posted to your ledger</span>
          </div>
        </div>

        <div className="expenses-form-wrap">
          <TransactionEntryForm
            allowedTypes={["expense"]}
            initialType="expense"
            title="Expense details"
            description="Choose where the money came from and what it was spent on."
          />
        </div>
      </section>

      <aside className="expenses-guidance">
        <div className="expenses-guidance-icon"><Info size={19} /></div>
        <div>
          <strong>Expenses and bills are tracked differently</strong>
          <p>
            Record everyday spending here. Use Bills for obligations with due
            dates and payment tracking. Every posted expense appears automatically
            in your Transactions history.
          </p>
        </div>
      </aside>
    </div>
  );
}
