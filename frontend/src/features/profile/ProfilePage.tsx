import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type SyntheticEvent } from "react";
import { endpoints } from "../../api/endpoints";
import type { ProfileConfig, ProfileConfigBody } from "../../api/types";
import { ApiErrorMessage } from "../../components/ApiErrorMessage";
import { Banner } from "../../components/Banner";
import { Loading } from "../../components/Loading";
import { useI18n } from "../../i18n/I18nProvider";
import { KnowledgePanel } from "./KnowledgePanel";
import { profileKeys } from "./keys";

const MIN_SCORE = 0;
const MAX_SCORE = 100;

interface FormValues {
  readonly field: string;
  readonly skills: string;
  readonly level: string;
  readonly minScore: string;
  readonly jobFeed: string;
  readonly certFeed: string;
  readonly regions: string;
  readonly languages: string;
  readonly offerType: string;
}

/** A comma-separated field becomes a list: blanks and repeats are dropped, order is kept. */
function listOf(value: string): string[] {
  const seen = new Set<string>();
  for (const part of value.split(",")) {
    const item = part.trim();
    if (item !== "") seen.add(item);
  }
  return [...seen];
}

function valuesOf(profile: ProfileConfig): FormValues {
  return {
    field: profile.field,
    skills: profile.skills,
    level: profile.level,
    minScore: String(profile.min_match_score),
    jobFeed: profile.job_feed_url ?? "",
    certFeed: profile.cert_feed_url ?? "",
    regions: (profile.regions ?? []).join(", "),
    languages: (profile.languages ?? []).join(", "),
    offerType: profile.offer_type ?? "",
  };
}

/** Empty feed fields are sent as null: "no feed", not an empty address. */
function bodyOf(values: FormValues): ProfileConfigBody {
  return {
    regions: listOf(values.regions),
    languages: listOf(values.languages),
    offer_type: values.offerType.trim() === "" ? null : values.offerType.trim(),
    field: values.field.trim(),
    skills: values.skills.trim(),
    level: values.level.trim(),
    min_match_score: Number(values.minScore),
    job_feed_url: values.jobFeed.trim() === "" ? null : values.jobFeed.trim(),
    cert_feed_url: values.certFeed.trim() === "" ? null : values.certFeed.trim(),
  };
}

function isValidScore(value: string): boolean {
  const number = Number(value);
  return (
    value.trim() !== "" && Number.isInteger(number) && number >= MIN_SCORE && number <= MAX_SCORE
  );
}

export function ProfilePage() {
  const { t } = useI18n();
  const query = useQuery({
    queryKey: profileKeys.profile,
    queryFn: ({ signal }) => endpoints.getProfile(signal),
  });

  return (
    <section>
      <div className="page-header">
        <div>
          <h1>{t("profile.title")}</h1>
          <p className="muted">{t("profile.intro")}</p>
        </div>
      </div>

      {query.isPending ? <Loading /> : null}
      {query.isError ? <ApiErrorMessage error={query.error} /> : null}
      {query.isSuccess ? (
        // Keyed on the saved content: after a save the form starts again from what the server stored.
        <ProfileForm key={JSON.stringify(query.data)} profile={query.data} />
      ) : null}

      <KnowledgePanel />
    </section>
  );
}

function ProfileForm({ profile }: { readonly profile: ProfileConfig }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const ids = {
    field: useId(),
    skills: useId(),
    level: useId(),
    minScore: useId(),
    jobFeed: useId(),
    certFeed: useId(),
    regions: useId(),
    languages: useId(),
    offerType: useId(),
  };
  const [values, setValues] = useState<FormValues>(() => valuesOf(profile));
  const [scoreInvalid, setScoreInvalid] = useState(false);

  const save = useMutation({
    mutationFn: (body: ProfileConfig) => endpoints.saveProfile(body),
    onSuccess: (saved) => {
      queryClient.setQueryData(profileKeys.profile, saved);
    },
  });

  function update(key: keyof FormValues, value: string) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  function onSubmit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    const valid = isValidScore(values.minScore);
    setScoreInvalid(!valid);
    if (!valid) return;
    save.mutate(bodyOf(values));
  }

  return (
    <form className="card" onSubmit={onSubmit}>
      <div className="field">
        <label htmlFor={ids.field}>{t("profile.fieldLabel")}</label>
        <input
          id={ids.field}
          className="input"
          value={values.field}
          aria-describedby={`${ids.field}-hint`}
          onChange={(event) => {
            update("field", event.target.value);
          }}
        />
        <p id={`${ids.field}-hint`} className="hint">
          {t("profile.fieldHelp")}
        </p>
      </div>

      <div className="field">
        <label htmlFor={ids.skills}>{t("profile.skillsLabel")}</label>
        <input
          id={ids.skills}
          className="input"
          value={values.skills}
          aria-describedby={`${ids.skills}-hint`}
          onChange={(event) => {
            update("skills", event.target.value);
          }}
        />
        <p id={`${ids.skills}-hint`} className="hint">
          {t("profile.skillsHelp")}
        </p>
      </div>

      <div className="field">
        <label htmlFor={ids.level}>{t("profile.levelLabel")}</label>
        <input
          id={ids.level}
          className="input"
          value={values.level}
          aria-describedby={`${ids.level}-hint`}
          onChange={(event) => {
            update("level", event.target.value);
          }}
        />
        <p id={`${ids.level}-hint`} className="hint">
          {t("profile.levelHelp")}
        </p>
      </div>

      <div className="field">
        <label htmlFor={ids.minScore}>{t("profile.minScoreLabel")}</label>
        <input
          id={ids.minScore}
          className="input"
          type="number"
          min={MIN_SCORE}
          max={MAX_SCORE}
          step={1}
          value={values.minScore}
          aria-invalid={scoreInvalid}
          aria-describedby={`${ids.minScore}-hint`}
          onChange={(event) => {
            update("minScore", event.target.value);
          }}
        />
        <p id={`${ids.minScore}-hint`} className="hint">
          {t("profile.minScoreHelp")}
        </p>
        {scoreInvalid ? <p className="field-error">{t("profile.minScoreLabel")}</p> : null}
      </div>

      <div className="field">
        <label htmlFor={ids.jobFeed}>{t("profile.jobFeedLabel")}</label>
        <input
          id={ids.jobFeed}
          className="input"
          type="url"
          value={values.jobFeed}
          onChange={(event) => {
            update("jobFeed", event.target.value);
          }}
        />
      </div>

      <div className="field">
        <label htmlFor={ids.certFeed}>{t("profile.certFeedLabel")}</label>
        <input
          id={ids.certFeed}
          className="input"
          type="url"
          value={values.certFeed}
          onChange={(event) => {
            update("certFeed", event.target.value);
          }}
        />
      </div>

      <div className="field">
        <label htmlFor={ids.regions}>{t("profile.regionsLabel")}</label>
        <input
          id={ids.regions}
          className="input"
          value={values.regions}
          aria-describedby={`${ids.regions}-hint`}
          onChange={(event) => {
            update("regions", event.target.value);
          }}
        />
        <p id={`${ids.regions}-hint`} className="hint">
          {t("profile.regionsHelp")}
        </p>
      </div>

      <div className="field">
        <label htmlFor={ids.languages}>{t("profile.languagesLabel")}</label>
        <input
          id={ids.languages}
          className="input"
          value={values.languages}
          aria-describedby={`${ids.languages}-hint`}
          onChange={(event) => {
            update("languages", event.target.value);
          }}
        />
        <p id={`${ids.languages}-hint`} className="hint">
          {t("profile.languagesHelp")}
        </p>
      </div>

      <div className="field">
        <label htmlFor={ids.offerType}>{t("profile.offerTypeLabel")}</label>
        <input
          id={ids.offerType}
          className="input"
          value={values.offerType}
          aria-describedby={`${ids.offerType}-hint`}
          onChange={(event) => {
            update("offerType", event.target.value);
          }}
        />
        <p id={`${ids.offerType}-hint`} className="hint">
          {t("profile.offerTypeHelp")}
        </p>
      </div>

      <ApiErrorMessage error={save.error} />
      {save.isSuccess ? <Banner tone="success">{t("profile.saved")}</Banner> : null}

      <div className="actions">
        <button type="submit" className="button button-primary" disabled={save.isPending}>
          {t("profile.save")}
        </button>
      </div>
    </form>
  );
}
