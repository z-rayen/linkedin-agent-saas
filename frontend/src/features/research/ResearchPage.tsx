import { useMutation } from "@tanstack/react-query";
import { useEffect, useId, useState, type SyntheticEvent } from "react";
import { endpoints } from "../../api/endpoints";
import { ApiErrorMessage } from "../../components/ApiErrorMessage";
import { Banner } from "../../components/Banner";
import { RunProgress } from "../../components/RunProgress";
import { followRun } from "../../api/runFollow";
import type { ResearchLink, ResearchResult, ResearchRun } from "../../api/types";
import { useI18n, type Translate } from "../../i18n/I18nProvider";
import { safeWebUrl } from "../../lib/safeUrl";
import styles from "./research.module.css";

const MAX_QUESTION_CHARS = 2000;
const TERMINAL = ["done", "failed"];

interface Turn {
  readonly runId: string;
  readonly question: string;
  readonly draftPost: boolean;
}

/** Research: ask a question, follow the run, read a sourced answer. The answer says what it rests on. */
export function ResearchPage() {
  const { t } = useI18n();
  const questionId = useId();
  const draftId = useId();
  const [question, setQuestion] = useState("");
  const [draftPost, setDraftPost] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);

  const start = useMutation({
    mutationFn: (body: { request: string; draft_post: boolean }) => endpoints.startResearch(body),
    onSuccess: (run, body) => {
      setTurns((current) => [
        ...current,
        { runId: run.run_id, question: body.request, draftPost: body.draft_post },
      ]);
      setQuestion("");
    },
  });

  function onSubmit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    const request = question.trim();
    if (request === "") return;
    start.mutate({ request, draft_post: draftPost });
  }

  return (
    <section>
      <div className="page-header">
        <div>
          <h1>{t("research.title")}</h1>
          <p className="muted">{t("research.intro")}</p>
        </div>
      </div>

      <form className="card" onSubmit={onSubmit}>
        <div className="field">
          <label htmlFor={questionId}>{t("research.questionLabel")}</label>
          <textarea
            id={questionId}
            className="textarea"
            rows={3}
            maxLength={MAX_QUESTION_CHARS}
            value={question}
            aria-describedby={`${questionId}-hint`}
            onChange={(event) => {
              setQuestion(event.target.value);
            }}
          />
          <p id={`${questionId}-hint`} className="hint">
            {t("research.questionHint")}
          </p>
        </div>
        <label className={styles.checkbox} htmlFor={draftId}>
          <input
            id={draftId}
            type="checkbox"
            checked={draftPost}
            onChange={(event) => {
              setDraftPost(event.target.checked);
            }}
          />{" "}
          {t("research.draftPost")}
        </label>
        <ApiErrorMessage error={start.error} />
        <div className="actions">
          <button
            type="submit"
            className="button button-primary"
            disabled={start.isPending || question.trim() === ""}
          >
            {t("research.submit")}
          </button>
        </div>
      </form>

      {turns.length === 0 ? <p className="muted">{t("research.empty")}</p> : null}

      {turns.map((turn) => (
        <ResearchTurn key={turn.runId} turn={turn} />
      ))}
    </section>
  );
}

/** One question and its answer. Following the run is local to the turn; it stops when the turn is left. */
function ResearchTurn({ turn }: { readonly turn: Turn }) {
  const { t } = useI18n();
  const [run, setRun] = useState<ResearchRun | null>(null);
  const [failure, setFailure] = useState<"failed" | "notFound" | null>(null);

  useEffect(
    () =>
      followRun(
        turn.runId,
        {
          fetchRun: (id) => endpoints.getResearch(id),
          isTerminal: (status) => TERMINAL.includes(status),
        },
        {
          onUpdate: (current) => {
            setRun(current);
          },
          onSettled: (current) => {
            if (current.status === "failed") setFailure("failed");
          },
          onError: (reason) => {
            setFailure(reason === "not_found" ? "notFound" : "failed");
          },
        },
      ),
    [turn.runId],
  );

  return (
    <article className="card" aria-label={turn.question}>
      <p className={styles.question}>
        <strong>{t("research.questionLabel")}: </strong>
        {turn.question}
      </p>

      {failure === "notFound" ? <Banner tone="danger">{t("research.notFound")}</Banner> : null}
      {failure === "failed" ? <Banner tone="danger">{t("research.failed")}</Banner> : null}
      {failure === null && run?.status !== "done" ? (
        <div>
          <RunProgress
            statusText={run === null ? t("research.queued") : t("research.running")}
            stepText=""
            elapsedLabel={(time) => t("common.elapsed", { time })}
            stalled={run?.stalled === true}
            stalledText={t("research.stalled")}
          />
          <p className="hint">{t("research.followingHint")}</p>
        </div>
      ) : null}

      {run?.status === "done" && run.result !== null ? (
        <ResearchAnswer result={run.result} draftPost={turn.draftPost} />
      ) : null}
    </article>
  );
}

function basisLabel(basis: string, t: Translate): string {
  if (basis === "fetched") return t("research.basisFetched");
  if (basis === "snippet") return t("research.basisSnippet");
  return t("research.basisNone");
}

function reasonLabel(reason: string, t: Translate): string {
  switch (reason) {
    case "uncited":
      return t("research.unsupportedReason.uncited");
    case "relative_time":
      return t("research.unsupportedReason.relative_time");
    case "numbers_not_found":
      return t("research.unsupportedReason.numbers_not_found");
    case "numbers_snippet_only":
      return t("research.unsupportedReason.numbers_snippet_only");
    case "entities_not_found":
      return t("research.unsupportedReason.entities_not_found");
    default:
      return t("research.unsupportedOther");
  }
}

/** The answer as text (never as HTML), its claims with their basis, the sources, and what the checks removed. */
export function ResearchAnswer({
  result,
  draftPost,
}: {
  readonly result: ResearchResult;
  readonly draftPost: boolean;
}) {
  const { t } = useI18n();
  const removed = Object.entries(result.unsupported).filter(([, count]) => count > 0);
  const removedTotal = removed.reduce((sum, [, count]) => sum + count, 0);

  return (
    <div className={styles.answer}>
      <h2>{t("research.answerTitle")}</h2>
      <p className={styles.text}>
        {result.answer.trim() === "" ? t("research.noAnswer") : result.answer}
      </p>
      {result.retrieved_at ? (
        <p className="muted">{t("research.retrievedAt", { date: result.retrieved_at })}</p>
      ) : null}
      {draftPost && result.post_id !== null ? (
        <Banner tone="success">{t("research.draftSaved")}</Banner>
      ) : null}

      <h3>{t("research.claimsTitle")}</h3>
      {result.claims.length === 0 ? (
        <p className="muted">{t("research.noAnswer")}</p>
      ) : (
        <ul className={styles.list}>
          {result.claims.map((claim, index) => (
            <li key={index}>
              <span>{claim.text}</span>{" "}
              <span
                className={claim.basis === "fetched" ? "chip chip-success" : "chip chip-warning"}
              >
                {basisLabel(claim.basis, t)}
              </span>
              {claim.sources.length > 0 ? (
                <span className="muted"> [{claim.sources.join(", ")}]</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      <h3>{t("research.sourcesTitle")}</h3>
      <ol className={styles.list}>
        {result.sources.map((source) => (
          <SourceItem key={`${String(source.number)}-${source.link}`} source={source} />
        ))}
      </ol>

      {result.also_found.length > 0 ? (
        <details className={styles.details}>
          <summary>
            {t("research.alsoFound")} ({result.also_found.length})
          </summary>
          <ul className={styles.list}>
            {result.also_found.map((source) => (
              <SourceItem key={source.link} source={source} />
            ))}
          </ul>
        </details>
      ) : null}

      <h3>{t("research.unsupportedTitle")}</h3>
      {removedTotal === 0 ? <p className="muted">{t("research.unsupportedNone")}</p> : null}
      <ul className={styles.list}>
        {removed.map(([reason, count]) => (
          <li key={reason}>
            {t("research.unsupportedLine", { count, reason: reasonLabel(reason, t) })}
          </li>
        ))}
      </ul>
      {result.off_topic > 0 ? (
        <p className="muted">{t("research.offTopic", { count: String(result.off_topic) })}</p>
      ) : null}

      <Banner tone="warning">{t("research.verifyReminder")}</Banner>
    </div>
  );
}

function SourceItem({ source }: { readonly source: ResearchLink }) {
  const { t } = useI18n();
  const href = safeWebUrl(source.link);
  return (
    <li>
      {href !== null ? (
        <a href={href} target="_blank" rel="noopener noreferrer">
          {source.title || href}
        </a>
      ) : (
        <span>{source.title || source.link}</span>
      )}{" "}
      <span className={source.fetched ? "chip chip-success" : "chip chip-warning"}>
        {source.fetched ? t("research.sourceFetched") : t("research.sourceSnippet")}
      </span>
      {href !== null ? (
        <span className="visually-hidden"> ({t("research.openSource")})</span>
      ) : null}
    </li>
  );
}
