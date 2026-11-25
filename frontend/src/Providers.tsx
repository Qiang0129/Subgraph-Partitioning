import { useEffect, useRef, useState } from "react";

const efforts = [
  ["default", "接口默认"],
  ["none", "不启用思考 · none"],
  ["minimal", "最低 · minimal"],
  ["low", "低 · low"],
  ["medium", "中 · medium"],
  ["high", "高 · high"],
  ["xhigh", "超高 · xhigh"],
  ["max", "最大 · max"],
] as const;
type Effort = (typeof efforts)[number][0];
export type Provider = {
  id: string;
  name: string;
  model: string;
  protocol: "chat" | "responses";
  base_url: string;
  verified: boolean;
  has_key?: boolean;
  reasoning_effort?: Effort;
  timeout_seconds?: number;
};
type Form = Omit<Provider, "id" | "verified"> & {
  id?: string;
  api_key: string;
};
const emptyForm = (): Form => ({
  name: "",
  protocol: "chat",
  base_url: "",
  model: "",
  api_key: "",
  reasoning_effort: "default",
});
const effortName = (value?: string) =>
  efforts.find(([key]) => key === value)?.[1] || "接口默认";

export function Providers({
  providers,
  reload,
  onClose,
  notify,
  request,
}: {
  providers: Provider[];
  reload: () => Promise<void>;
  onClose: () => void;
  notify: (message: string) => void;
  request: (path: string, method?: string, body?: unknown) => Promise<any>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [form, setForm] = useState<Form>(emptyForm);
  const [busy, setBusy] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [feedback, setFeedback] = useState<{
    error: boolean;
    text: string;
    scope?: "models";
  } | null>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  const reset = (next = emptyForm()) => {
    setForm(next);
    setModels([]);
    setLoaded(false);
    setQuery("");
    setFeedback(null);
  };
  const connectionChanged = (changes: Partial<Form>) => {
    setForm((previous) => ({ ...previous, ...changes }));
    setModels([]);
    setLoaded(false);
    setQuery("");
    setFeedback(null);
  };
  const perform = async (action: () => Promise<void>, key: string) => {
    setBusy(key);
    setFeedback(null);
    try {
      await action();
    } catch (error) {
      setFeedback({
        error: true,
        text: (error as Error).message,
        scope: key === "models" ? "models" : undefined,
      });
    } finally {
      setBusy("");
    }
  };
  const fetchModels = () =>
    perform(async () => {
      const result = await request("/providers/models", "POST", {
        id: form.id,
        base_url: form.base_url,
        api_key: form.api_key,
      });
      setModels(result.models);
      setLoaded(true);
      setQuery("");
      setFeedback({
        error: false,
        scope: "models",
        text: result.models.length
          ? `已获取 ${result.models.length} 个模型。请选择模型，并测试视觉与工具调用能力。`
          : "接口返回的模型列表为空。你仍可手动填写模型 ID。",
      });
    }, "models");
  const visibleModels = models.filter((model) =>
    model.toLowerCase().includes(query.trim().toLowerCase()),
  );

  return (
    <dialog
      ref={dialog}
      onCancel={onClose}
      className="provider-dialog"
      aria-labelledby="providers-title"
    >
      <header>
        <div>
          <h2 id="providers-title">模型供应商</h2>
          <p>连接你的接口，选择模型并设置思考程度。</p>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭模型配置">
          ✕
        </button>
      </header>
      <div className="provider-grid">
        <section
          className="provider-list"
          aria-labelledby="saved-providers-title"
        >
          <div className="provider-section-heading">
            <h3 id="saved-providers-title">
              已配置接口{" "}
              <span className="provider-count">{providers.length}</span>
            </h3>
            <button type="button" disabled={!!busy} onClick={() => reset()}>
              新增
            </button>
          </div>
          {providers.length === 0 ? (
            <p className="hint">
              还没有接口。先填写右侧的地址和密钥，再获取可用模型。
            </p>
          ) : (
            providers.map((provider) => (
              <div
                className={
                  "provider-row" +
                  (form.id === provider.id ? " is-editing" : "")
                }
                key={provider.id}
              >
                <strong>{provider.name}</strong>
                <span>{provider.model}</span>
                <span>
                  {provider.protocol === "responses"
                    ? "Responses"
                    : "Chat Completions"}{" "}
                  · {effortName(provider.reasoning_effort)}
                </span>
                <span
                  className={
                    "status " + (provider.verified ? "CONFIRMED" : "DRAFT")
                  }
                >
                  {provider.verified ? "能力测试通过" : "尚未验证"}
                </span>
                <div className="inline-actions">
                  <button
                    type="button"
                    disabled={!!busy}
                    onClick={() =>
                      reset({
                        ...provider,
                        api_key: "",
                        reasoning_effort:
                          provider.reasoning_effort || "default",
                      })
                    }
                  >
                    编辑
                  </button>
                  <button
                    type="button"
                    disabled={!!busy}
                    onClick={() =>
                      perform(async () => {
                        await request(
                          `/providers/${provider.id}/probe`,
                          "POST",
                          {},
                        );
                        await reload();
                        setFeedback({
                          error: false,
                          text: `“${provider.name}”的视觉、工具调用和结果回传测试通过。`,
                        });
                      }, provider.id)
                    }
                  >
                    {busy === provider.id ? "正在测试…" : "测试能力"}
                  </button>
                </div>
              </div>
            ))
          )}
        </section>
        <form
          className="provider-form"
          onSubmit={(event) => {
            event.preventDefault();
            perform(async () => {
              await request("/providers", "POST", form);
              await reload();
              reset();
              setFeedback({
                error: false,
                text: "配置已保存。请点击左侧“测试能力”，验证当前模型和思考程度。",
              });
              notify("模型配置已保存，需要重新测试能力");
            }, "save");
          }}
        >
          <div className="provider-form-heading">
            <h3>{form.id ? "编辑接口" : "新增接口"}</h3>
            <p className="hint">先连接，再选模型。密钥由 Windows 加密保存。</p>
          </div>
          <fieldset disabled={!!busy} className="provider-fields">
            <div className="provider-field-row">
              <label>
                配置名称
                <input
                  required
                  value={form.name}
                  placeholder="例如：科研图组主模型"
                  onChange={(event) =>
                    setForm({ ...form, name: event.target.value })
                  }
                />
              </label>
              <label>
                协议
                <select
                  value={form.protocol}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      protocol: event.target.value as Form["protocol"],
                    })
                  }
                >
                  <option value="chat">Chat Completions</option>
                  <option value="responses">Responses</option>
                </select>
              </label>
            </div>
            <label>
              接口地址
              <input
                required
                type="url"
                placeholder="https://your-provider.example/v1"
                value={form.base_url}
                onChange={(event) =>
                  connectionChanged({ base_url: event.target.value })
                }
              />
            </label>
            <label>
              API Key
              <input
                type="password"
                autoComplete="off"
                required={!form.id}
                placeholder={
                  form.has_key ? "留空使用已保存的密钥" : "输入接口密钥"
                }
                value={form.api_key}
                onChange={(event) =>
                  connectionChanged({ api_key: event.target.value })
                }
              />
            </label>
            <section
              className="provider-model-picker"
              aria-labelledby="model-picker-title"
              aria-busy={busy === "models"}
            >
              <div className="provider-section-heading">
                <h4 id="model-picker-title">选择模型</h4>
                <button
                  type="button"
                  className="model-fetch"
                  disabled={
                    !form.base_url.trim() ||
                    (!form.api_key.trim() && !form.has_key)
                  }
                  onClick={fetchModels}
                >
                  {busy === "models"
                    ? "正在获取…"
                    : loaded
                      ? "刷新模型列表"
                      : "获取模型列表"}
                </button>
              </div>
              {feedback?.scope === "models" && (
                <div
                  className={
                    "provider-feedback" + (feedback.error ? " is-error" : "")
                  }
                  role={feedback.error ? "alert" : "status"}
                >
                  {feedback.text}
                </div>
              )}
              {!loaded && (
                <p className="hint">
                  填写地址和密钥后获取模型，也可直接输入模型 ID。
                </p>
              )}
              {busy === "models" && (
                <p className="hint" role="status">
                  正在连接接口，读取可用模型…
                </p>
              )}
              {loaded && models.length > 0 && (
                <div className="provider-model-results">
                  <div className="provider-model-filter">
                    <input
                      type="search"
                      aria-label="筛选模型"
                      placeholder="搜索模型名称…"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                    />
                    <span>
                      {visibleModels.length} / {models.length}
                    </span>
                  </div>
                  {visibleModels.length > 0 ? (
                    <select
                      aria-label="可用模型"
                      size={5}
                      value={
                        visibleModels.includes(form.model) ? form.model : ""
                      }
                      onChange={(event) =>
                        setForm({ ...form, model: event.target.value })
                      }
                    >
                      <option value="" disabled>
                        选择一个模型
                      </option>
                      {visibleModels.map((model) => (
                        <option key={model} value={model}>
                          {model}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <p className="hint">
                      没有匹配的模型，请更换搜索词或手动填写。
                    </p>
                  )}
                </div>
              )}
              {loaded && models.length === 0 && (
                <p className="hint">接口未返回可用模型，请手动填写。</p>
              )}
              <label>
                模型 ID
                <input
                  required
                  value={form.model}
                  placeholder="选择上方模型，或手动填写"
                  onChange={(event) =>
                    setForm({ ...form, model: event.target.value })
                  }
                />
              </label>
            </section>
            <label>
              思考程度
              <select
                value={form.reasoning_effort || "default"}
                aria-describedby="reasoning-help"
                onChange={(event) =>
                  setForm({
                    ...form,
                    reasoning_effort: event.target.value as Effort,
                  })
                }
              >
                {efforts.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <p className="hint" id="reasoning-help">
              更高档位通常需要更长时间。各模型支持的档位不同；“接口默认”不额外指定思考程度。
            </p>
          </fieldset>
          {feedback && feedback.scope !== "models" && (
            <div
              className={
                "provider-feedback" + (feedback.error ? " is-error" : "")
              }
              role={feedback.error ? "alert" : "status"}
            >
              {feedback.text}
            </div>
          )}
          <footer className="provider-form-footer">
            <p className="hint">
              获取列表不调用生成模型；“测试能力”会发送一张系统生成的数字图片，并产生少量调用。
            </p>
            <div className="inline-actions">
              <button className="primary" disabled={!!busy}>
                {busy === "save" ? "保存中…" : "保存配置"}
              </button>
              {form.id && (
                <button type="button" disabled={!!busy} onClick={() => reset()}>
                  取消编辑
                </button>
              )}
            </div>
          </footer>
        </form>
      </div>
    </dialog>
  );
}
