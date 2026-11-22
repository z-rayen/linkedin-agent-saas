import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type SyntheticEvent } from "react";
import { endpoints } from "../../api/endpoints";
import { ApiErrorMessage } from "../../components/ApiErrorMessage";
import { Banner } from "../../components/Banner";
import { EmptyState } from "../../components/EmptyState";
import { Loading } from "../../components/Loading";
import { useI18n } from "../../i18n/I18nProvider";
import { profileKeys } from "./keys";

/** The knowledge base: short facts about the user, read when a preparation looks for sources. Deleting needs a second
 * click, because a deleted entry stops being used at once. */
export function KnowledgePanel() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const addId = useId();
  const [draft, setDraft] = useState("");
  const [pendingDelete, setPendingDelete] = useState<number | null>(null);

  const query = useQuery({
    queryKey: profileKeys.knowledge,
    queryFn: ({ signal }) => endpoints.listKnowledge(signal),
  });

  const add = useMutation({
    mutationFn: (content: string) => endpoints.addKnowledge(content),
    onSuccess: () => {
      setDraft("");
      void queryClient.invalidateQueries({ queryKey: profileKeys.knowledge });
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => endpoints.deleteKnowledge(id),
    onSuccess: () => {
      setPendingDelete(null);
      void queryClient.invalidateQueries({ queryKey: profileKeys.knowledge });
    },
  });

  function onAdd(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = draft.trim();
    if (content !== "") add.mutate(content);
  }

  return (
    <section className="card" aria-labelledby={`${addId}-title`}>
      <h2 id={`${addId}-title`}>{t("knowledge.title")}</h2>
      <p className="muted">{t("knowledge.explain")}</p>

      {query.isPending ? <Loading /> : null}
      {query.isError ? <ApiErrorMessage error={query.error} /> : null}

      {query.isSuccess && query.data.length === 0 ? (
        <EmptyState>{t("knowledge.empty")}</EmptyState>
      ) : null}

      {query.isSuccess && query.data.length > 0 ? (
        <ul className="stack">
          {query.data.map((item) => (
            <li key={item.id} className="card">
              <p>{item.content}</p>
              {pendingDelete === item.id ? (
                <div className="actions">
                  <span>{t("knowledge.confirmRemove")}</span>
                  <button
                    type="button"
                    className="button"
                    disabled={remove.isPending}
                    onClick={() => {
                      remove.mutate(item.id);
                    }}
                  >
                    {t("knowledge.confirm")}
                  </button>
                  <button
                    type="button"
                    className="button"
                    onClick={() => {
                      setPendingDelete(null);
                    }}
                  >
                    {t("knowledge.cancel")}
                  </button>
                </div>
              ) : (
                <div className="actions">
                  <button
                    type="button"
                    className="button"
                    onClick={() => {
                      setPendingDelete(item.id);
                    }}
                  >
                    {t("knowledge.remove")}
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      ) : null}
      <ApiErrorMessage error={remove.error} />
      {remove.isSuccess ? <Banner tone="success">{t("knowledge.removed")}</Banner> : null}

      <form onSubmit={onAdd}>
        <div className="field">
          <label htmlFor={addId}>{t("knowledge.addLabel")}</label>
          <textarea
            id={addId}
            className="textarea"
            rows={3}
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value);
            }}
          />
        </div>
        <ApiErrorMessage error={add.error} />
        {add.isSuccess ? <Banner tone="success">{t("knowledge.added")}</Banner> : null}
        <button
          type="submit"
          className="button button-primary"
          disabled={add.isPending || draft.trim() === ""}
        >
          {t("knowledge.add")}
        </button>
      </form>
    </section>
  );
}
