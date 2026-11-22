import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type SyntheticEvent } from "react";
import { endpoints } from "../../api/endpoints";
import type { ContentItem, ContentStatus } from "../../api/types";
import { ApiErrorMessage } from "../../components/ApiErrorMessage";
import { Banner } from "../../components/Banner";
import { EmptyState } from "../../components/EmptyState";
import { Loading } from "../../components/Loading";
import { useI18n, type MessageKey } from "../../i18n/I18nProvider";
import { contentKeys } from "./keys";

const STATUS_CHIP: Record<ContentStatus, string> = {
  draft: "chip chip-info",
  approved: "chip chip-info",
  published: "chip chip-success",
  error: "chip chip-danger",
};

const STATUS_KEY: Record<ContentStatus, MessageKey> = {
  draft: "content.statusDraft",
  approved: "content.statusApproved",
  published: "content.statusPublished",
  error: "content.statusError",
};

/** The content queue: generate drafts, edit them, publish them. Publishing always asks once more. */
export function ContentPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const topicId = useId();
  const contextId = useId();
  const [topic, setTopic] = useState("");
  const [context, setContext] = useState("");

  const query = useQuery({
    queryKey: contentKeys.queue,
    queryFn: ({ signal }) => endpoints.contentQueue(signal),
  });

  const generate = useMutation({
    mutationFn: () =>
      endpoints.generateContent({ title: topic.trim(), context: context.trim(), source: "manual" }),
    onSuccess: () => {
      setTopic("");
      setContext("");
      void queryClient.invalidateQueries({ queryKey: contentKeys.queue });
    },
  });

  function onGenerate(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (topic.trim() !== "" && context.trim() !== "") generate.mutate();
  }

  return (
    <section>
      <div className="page-header">
        <div>
          <h1>{t("content.title")}</h1>
          <p className="muted">{t("content.intro")}</p>
        </div>
      </div>

      <form className="card" onSubmit={onGenerate}>
        <h2>{t("content.newTitle")}</h2>
        <div className="field">
          <label htmlFor={topicId}>{t("content.topicLabel")}</label>
          <input
            id={topicId}
            className="input"
            value={topic}
            onChange={(event) => {
              setTopic(event.target.value);
            }}
          />
        </div>
        <div className="field">
          <label htmlFor={contextId}>{t("content.contextLabel")}</label>
          <textarea
            id={contextId}
            className="textarea"
            rows={4}
            value={context}
            onChange={(event) => {
              setContext(event.target.value);
            }}
          />
        </div>
        <ApiErrorMessage error={generate.error} />
        <button
          type="submit"
          className="button button-primary"
          disabled={generate.isPending || topic.trim() === "" || context.trim() === ""}
        >
          {generate.isPending ? t("content.generating") : t("content.generate")}
        </button>
      </form>

      {query.isPending ? <Loading /> : null}
      {query.isError ? <ApiErrorMessage error={query.error} /> : null}
      {query.isSuccess && query.data.length === 0 ? (
        <EmptyState>{t("content.empty")}</EmptyState>
      ) : null}

      {query.isSuccess ? (
        <ul className="stack">
          {query.data.map((item) => (
            <ContentCard key={item.id} item={item} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}

type Mode = "view" | "edit" | "confirm";

function ContentCard({ item }: { readonly item: ContentItem }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const titleId = useId();
  const textId = useId();
  const [mode, setMode] = useState<Mode>("view");
  const [title, setTitle] = useState(item.title);
  const [text, setText] = useState(item.post_text);
  const [published, setPublished] = useState(false);

  const edit = useMutation({
    mutationFn: () =>
      endpoints.editContent(item.id, { title: title.trim(), post_text: text.trim() }),
    onSuccess: () => {
      setMode("view");
      void queryClient.invalidateQueries({ queryKey: contentKeys.queue });
    },
  });

  const publish = useMutation({
    mutationFn: () => endpoints.publishContent(item.id),
    onSuccess: () => {
      setMode("view");
      setPublished(true);
      void queryClient.invalidateQueries({ queryKey: contentKeys.queue });
    },
  });

  const isPublished = item.status === "published";

  return (
    <li className="card">
      <div className="page-header">
        <h2>{item.title}</h2>
        <span className={STATUS_CHIP[item.status]}>{t(STATUS_KEY[item.status])}</span>
      </div>

      {mode === "edit" ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            edit.mutate();
          }}
        >
          <div className="field">
            <label htmlFor={titleId}>{t("content.topicLabel")}</label>
            <input
              id={titleId}
              className="input"
              value={title}
              onChange={(event) => {
                setTitle(event.target.value);
              }}
            />
          </div>
          <div className="field">
            <label htmlFor={textId}>{t("content.statusDraft")}</label>
            <textarea
              id={textId}
              className="textarea"
              rows={8}
              value={text}
              onChange={(event) => {
                setText(event.target.value);
              }}
            />
          </div>
          <ApiErrorMessage error={edit.error} />
          <div className="actions">
            <button
              type="submit"
              className="button button-primary"
              disabled={edit.isPending || text.trim() === ""}
            >
              {t("content.save")}
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                setTitle(item.title);
                setText(item.post_text);
                setMode("view");
              }}
            >
              {t("content.cancel")}
            </button>
          </div>
        </form>
      ) : (
        <p className="pre-wrap">{item.post_text}</p>
      )}

      {mode === "confirm" ? (
        <div className="card" role="alertdialog" aria-labelledby={`${titleId}-confirm`}>
          <h3 id={`${titleId}-confirm`}>{t("content.publishTitle")}</h3>
          <p>{t("content.publishBody")}</p>
          <ApiErrorMessage error={publish.error} />
          <div className="actions">
            <button
              type="button"
              className="button button-primary"
              disabled={publish.isPending}
              onClick={() => {
                publish.mutate();
              }}
            >
              {t("content.publishConfirm")}
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                setMode("view");
              }}
            >
              {t("content.cancel")}
            </button>
          </div>
        </div>
      ) : null}

      {published || isPublished ? <Banner tone="success">{t("content.published")}</Banner> : null}

      {mode === "view" && !isPublished ? (
        <div className="actions">
          <button
            type="button"
            className="button"
            onClick={() => {
              setMode("edit");
            }}
          >
            {t("content.edit")}
          </button>
          <button
            type="button"
            className="button button-primary"
            onClick={() => {
              setMode("confirm");
            }}
          >
            {t("content.publish")}
          </button>
        </div>
      ) : null}
      {isPublished ? <p className="muted">{t("content.publishedNoEdit")}</p> : null}
    </li>
  );
}
