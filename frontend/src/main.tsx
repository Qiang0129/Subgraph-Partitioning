import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Stage,
  Layer,
  Image as CanvasImage,
  Rect,
  Text,
  Transformer,
} from "react-konva";
import "./style.css";

type Region = {
  id: string;
  label: string;
  rect: [number, number, number, number];
  panel_ids: string[];
};
type Layout = {
  schema_version?: string;
  image_size: [number, number];
  panels: Region[];
  shared_regions: Region[];
};
type Picture = {
  id: string;
  name: string;
  status: string;
  revision: number;
  image_size?: [number, number];
  layout?: Layout;
  issues?: { code: string; message: string; severity: string }[];
  error?: string;
  events?: { action: string; message: string }[];
  calls?: number;
  versions?: { revision: number; status: string }[];
};
type Provider = {
  id: string;
  name: string;
  model: string;
  protocol: string;
  base_url: string;
  verified: boolean;
  has_key?: boolean;
};
const names: Record<string, string> = {
  DRAFT: "草稿",
  QUEUED: "排队中",
  RUNNING: "分析中",
  NEEDS_REVIEW: "待人工复核",
  CONFIRMED: "人工确认",
  AUTO_ACCEPTED: "自动通过",
  FAILED: "失败",
  INTERRUPTED: "已中断",
  CANCELLING: "正在取消",
  CANCELLED: "已取消",
};
const locked = (p: Picture | null) =>
  !!p && ["RUNNING", "QUEUED", "CANCELLING"].includes(p.status);
async function api(path: string, method = "GET", body?: unknown) {
  const response = await fetch("/api" + path, {
    method,
    headers:
      body instanceof FormData
        ? {}
        : body
          ? { "Content-Type": "application/json" }
          : {},
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response
      .json()
      .catch(() => ({ message: `请求失败 (${response.status})` }));
    throw new Error(
      data.message || data.detail || `请求失败 (${response.status})`,
    );
  }
  return response.status === 204 ||
    response.headers.get("content-length") === "0"
    ? null
    : response.text().then((t) => (t ? JSON.parse(t) : null));
}
const asset = (id: string, file = "normalized.png", revision?: number) =>
  `/api/images/${id}/asset?file=${encodeURIComponent(file)}${revision === undefined ? "" : `&version=${revision}`}`;
const clone = <T,>(value: T): T => structuredClone(value);

function Editor({
  picture,
  layout,
  onChange,
  selected,
  setSelected,
  mode,
  setMode,
}: {
  picture: Picture;
  layout: Layout;
  onChange: (l: Layout) => void;
  selected: string;
  setSelected: (s: string) => void;
  mode: string;
  setMode: (s: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null),
    transformer = useRef<any>(null),
    nodes = useRef<Record<string, any>>({});
  const [image, setImage] = useState<HTMLImageElement | null>(null),
    [width, setWidth] = useState(760),
    [zoom, setZoom] = useState(1),
    [drawing, setDrawing] = useState<number[] | null>(null),
    [imageError, setImageError] = useState(false);
  const [w, h] = layout.image_size;
  const scale = Math.min((width - 24) / w, 1) * zoom;
  useEffect(() => {
    const element = host.current!;
    const observer = new ResizeObserver((entries) =>
      setWidth(entries[0].contentRect.width),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    setImage(null);
    setImageError(false);
    const img = new Image();
    img.onload = () => setImage(img);
    img.onerror = () => setImageError(true);
    img.src = asset(picture.id);
  }, [picture.id]);
  useEffect(() => {
    transformer.current?.nodes(
      selected && nodes.current[selected] ? [nodes.current[selected]] : [],
    );
    transformer.current?.getLayer()?.batchDraw();
  }, [selected, layout, mode]);
  const point = (stage: any) => {
    const p = stage.getPointerPosition();
    return [
      Math.max(0, Math.min(w, p.x / scale)),
      Math.max(0, Math.min(h, p.y / scale)),
    ];
  };
  const modify = (id: string, rect: number[]) => {
    const l = clone(layout);
    const region = [...l.panels, ...l.shared_regions].find((r) => r.id === id)!;
    let [x, y, x2, y2] = rect.map(Math.round);
    x = Math.max(0, Math.min(w - 2, x));
    y = Math.max(0, Math.min(h - 2, y));
    region.rect = [
      x,
      y,
      Math.max(x + 1, Math.min(w, x2)),
      Math.max(y + 1, Math.min(h, y2)),
    ];
    onChange(l);
  };
  const finish = (stage: any) => {
    if (!drawing) return;
    const p = point(stage);
    const [x1, y1, x2, y2] = [
      Math.min(drawing[0], p[0]),
      Math.min(drawing[1], p[1]),
      Math.max(drawing[0], p[0]),
      Math.max(drawing[1], p[1]),
    ].map(Math.round);
    setDrawing(null);
    if (x2 - x1 < 4 || y2 - y1 < 4) return;
    const l = clone(layout);
    const label =
      mode === "shared"
        ? "共享标题"
        : Array.from({ length: 26 }, (_, i) =>
            String.fromCharCode(65 + i),
          ).find((s) => !l.panels.some((r) => r.label === s)) ||
          `面板${l.panels.length + 1}`;
    const r: Region = {
      id: (mode === "shared" ? "s_" : "p_") + crypto.randomUUID().slice(0, 8),
      label,
      rect: [x1, y1, x2, y2],
      panel_ids: [],
    };
    (mode === "shared" ? l.shared_regions : l.panels).push(r);
    onChange(l);
    setSelected(r.id);
    setMode("select");
  };
  return (
    <section className="canvas-panel">
      <div className="canvas-toolbar">
        <div className="segmented" aria-label="编辑工具">
          {[
            ["select", "选择 / 调整"],
            ["panel", "添加面板"],
            ["shared", "添加共享标题"],
          ].map(([key, label]) => (
            <button
              key={key}
              disabled={locked(picture)}
              className={mode === key ? "chosen" : ""}
              onClick={() => setMode(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="zoom">
          <button
            onClick={() => setZoom(Math.max(0.25, zoom - 0.25))}
            aria-label="缩小"
          >
            −
          </button>
          <button onClick={() => setZoom(1)}>适应宽度</button>
          <button
            onClick={() => setZoom(Math.min(4, zoom + 0.25))}
            aria-label="放大"
          >
            ＋
          </button>
          <span>{Math.round(scale * 100)}%</span>
        </div>
      </div>
      <div className="canvas-scroll" ref={host}>
        {imageError ? (
          <p role="alert">原图加载失败，请检查 Agent 服务和文件记录。</p>
        ) : !image ? (
          <p className="loading">正在加载原图…</p>
        ) : (
          <Stage
            width={Math.max(width, w * scale + 24)}
            height={h * scale + 24}
            onMouseDown={(e) => {
              if (locked(picture)) return;
              if (mode !== "select") setDrawing(point(e.target.getStage()));
              else if (
                e.target === e.target.getStage() ||
                e.target.getClassName() === "Image"
              )
                setSelected("");
            }}
            onMouseMove={(e) => {
              if (drawing) {
                const p = point(e.target.getStage());
                setDrawing([drawing[0], drawing[1], ...p]);
              }
            }}
            onMouseUp={(e) => finish(e.target.getStage())}
            onTouchStart={(e) => {
              if (!locked(picture) && mode !== "select") {
                e.evt.preventDefault();
                setDrawing(point(e.target.getStage()));
              }
            }}
            onTouchMove={(e) => {
              if (drawing) {
                e.evt.preventDefault();
                const p = point(e.target.getStage());
                setDrawing([drawing[0], drawing[1], ...p]);
              }
            }}
            onTouchEnd={(e) => finish(e.target.getStage())}
          >
            <Layer scaleX={scale} scaleY={scale}>
              <CanvasImage image={image} width={w} height={h} />
              {[...layout.panels, ...layout.shared_regions].map((r) => {
                const shared = layout.shared_regions.some((x) => x.id === r.id);
                const color = shared
                  ? "oklch(0.48 0.15 50)"
                  : "oklch(0.45 0.074 200)";
                return (
                  <React.Fragment key={r.id}>
                    <Rect
                      ref={(node) => {
                        nodes.current[r.id] = node;
                      }}
                      x={r.rect[0]}
                      y={r.rect[1]}
                      width={r.rect[2] - r.rect[0]}
                      height={r.rect[3] - r.rect[1]}
                      stroke={color}
                      strokeWidth={(selected === r.id ? 3 : 1.5) / scale}
                      fill={
                        selected === r.id ? "rgba(0,100,110,.09)" : undefined
                      }
                      draggable={mode === "select" && !locked(picture)}
                      listening={mode === "select"}
                      onClick={() => setSelected(r.id)}
                      onTap={() => setSelected(r.id)}
                      onDragEnd={(e) => {
                        const n = e.target;
                        modify(r.id, [
                          n.x(),
                          n.y(),
                          n.x() + n.width(),
                          n.y() + n.height(),
                        ]);
                      }}
                      onTransformEnd={(e) => {
                        const n = e.target;
                        const rw = n.width() * n.scaleX(),
                          rh = n.height() * n.scaleY();
                        n.scaleX(1);
                        n.scaleY(1);
                        modify(r.id, [n.x(), n.y(), n.x() + rw, n.y() + rh]);
                      }}
                    />
                    <Text
                      x={r.rect[0] + 3 / scale}
                      y={r.rect[1] + 3 / scale}
                      text={r.label}
                      fontSize={13 / scale}
                      fill={color}
                      padding={2 / scale}
                      listening={false}
                    />
                  </React.Fragment>
                );
              })}
              {drawing && drawing.length === 4 && (
                <Rect
                  x={Math.min(drawing[0], drawing[2])}
                  y={Math.min(drawing[1], drawing[3])}
                  width={Math.abs(drawing[2] - drawing[0])}
                  height={Math.abs(drawing[3] - drawing[1])}
                  stroke="oklch(0.45 0.074 200)"
                  strokeWidth={2 / scale}
                  listening={false}
                />
              )}
              <Transformer
                ref={transformer}
                rotateEnabled={false}
                flipEnabled={false}
                enabledAnchors={
                  locked(picture) || mode !== "select" ? [] : undefined
                }
                boundBoxFunc={(oldBox, newBox) =>
                  newBox.width < 4 || newBox.height < 4 ? oldBox : newBox
                }
              />
            </Layer>
          </Stage>
        )}
      </div>
      <footer className="canvas-caption">
        原图坐标 · {w} × {h} px{" "}
        <span>
          {mode === "select"
            ? "拖动裁框或边角调整；右侧可输入精确坐标"
            : "在原图上按住并拖动，绘制一个矩形"}
        </span>
      </footer>
    </section>
  );
}

function Providers({
  providers,
  reload,
  onClose,
  notify,
}: {
  providers: Provider[];
  reload: () => Promise<void>;
  onClose: () => void;
  notify: (s: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [form, setForm] = useState<any>({
    name: "",
    protocol: "chat",
    base_url: "",
    model: "",
    api_key: "",
  });
  const [busy, setBusy] = useState("");
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  const perform = async (action: () => Promise<void>, key: string) => {
    setBusy(key);
    try {
      await action();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy("");
    }
  };
  return (
    <dialog ref={dialog} onCancel={onClose} className="provider-dialog">
      <header>
        <div>
          <h2>模型供应商</h2>
          <p>独立管理接口，启用前测试视觉输入与原生工具调用。</p>
        </div>
        <button onClick={onClose} aria-label="关闭模型配置">
          ✕
        </button>
      </header>
      <div className="provider-grid">
        <section>
          <h3>已配置接口</h3>
          {providers.length === 0 ? (
            <p>尚未配置。填写右侧表单添加你的视觉模型。</p>
          ) : (
            providers.map((p) => (
              <div className="provider-row" key={p.id}>
                <strong>{p.name}</strong>
                <span>
                  {p.model} · {p.protocol}
                </span>
                <span
                  className={"status " + (p.verified ? "CONFIRMED" : "DRAFT")}
                >
                  {p.verified ? "能力测试通过" : "尚未验证"}
                </span>
                <div className="inline-actions">
                  <button
                    disabled={!!busy}
                    onClick={() => {
                      setForm({ ...p, api_key: "" });
                    }}
                  >
                    编辑
                  </button>
                  <button
                    disabled={!!busy}
                    onClick={() =>
                      perform(async () => {
                        await api(`/providers/${p.id}/probe`, "POST", {});
                        await reload();
                        notify("视觉、工具调用和结果回传测试通过");
                      }, p.id)
                    }
                  >
                    {busy === p.id ? "正在测试…" : "测试能力"}
                  </button>
                </div>
              </div>
            ))
          )}
        </section>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            perform(async () => {
              await api("/providers", "POST", form);
              await reload();
              setForm({
                name: "",
                protocol: "chat",
                base_url: "",
                model: "",
                api_key: "",
              });
              notify("配置已保存，需要重新测试能力");
            }, "save");
          }}
        >
          <h3>{form.id ? "编辑接口" : "新增接口"}</h3>
          <label>
            配置名称
            <input
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          <label>
            协议
            <select
              value={form.protocol}
              onChange={(e) => setForm({ ...form, protocol: e.target.value })}
            >
              <option value="chat">Chat Completions</option>
              <option value="responses">Responses</option>
            </select>
          </label>
          <label>
            接口地址
            <input
              required
              type="url"
              placeholder="https://your-provider.example/v1"
              value={form.base_url}
              onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            />
          </label>
          <label>
            模型名称
            <input
              required
              value={form.model}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
            />
          </label>
          <label>
            API Key
            <input
              type="password"
              autoComplete="off"
              required={!form.id}
              placeholder={form.id ? "留空保留现有密钥" : ""}
              value={form.api_key}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
            />
          </label>
          <p className="hint">
            密钥由 Windows
            加密保存。测试会向该接口发送一张系统生成的数字图片，并产生少量调用。
          </p>
          <button className="primary" disabled={!!busy}>
            {busy === "save" ? "保存中…" : "保存配置"}
          </button>
          {form.id && (
            <button
              type="button"
              onClick={() =>
                setForm({
                  name: "",
                  protocol: "chat",
                  base_url: "",
                  model: "",
                  api_key: "",
                })
              }
            >
              取消编辑
            </button>
          )}
        </form>
      </div>
    </dialog>
  );
}

function App() {
  const [batches, setBatches] = useState<any[]>([]),
    [batchId, setBatchId] = useState(""),
    [pictures, setPictures] = useState<Picture[]>([]),
    [detail, setDetail] = useState<Picture | null>(null),
    [layout, setLayout] = useState<Layout | null>(null),
    [providers, setProviders] = useState<Provider[]>([]),
    [providerId, setProviderId] = useState(""),
    [showProviders, setShowProviders] = useState(false),
    [dirty, setDirty] = useState(false),
    [selected, setSelected] = useState(""),
    [mode, setMode] = useState("select"),
    [busy, setBusy] = useState(""),
    [message, setMessage] = useState(""),
    [filter, setFilter] = useState("all"),
    [search, setSearch] = useState(""),
    [healthy, setHealthy] = useState<boolean | null>(null),
    [version, setVersion] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  const notify = (text: string) => setMessage(text);
  const reloadProviders = async () => {
    const p = await api("/providers");
    setProviders(p);
    setProviderId(
      (current) => current || p.find((x: Provider) => x.verified)?.id || "",
    );
  };
  const refresh = async (id = batchId) => {
    if (id) setPictures(await api(`/batches/${id}/images`));
  };
  useEffect(() => {
    Promise.all([
      api("/batches").then(setBatches),
      reloadProviders(),
      api("/health").then((h) => setHealthy(h.agent)),
    ]).catch((e) => notify(e.message));
  }, []);
  useEffect(() => {
    if (!batchId && batches.length) setBatchId(batches[0].id);
  }, [batches]);
  useEffect(() => {
    setDetail(null);
    setLayout(null);
    setDirty(false);
    refresh().catch((e) => notify(e.message));
  }, [batchId]);
  useEffect(() => {
    let active = true;
    const stream = new EventSource("/api/events");
    stream.addEventListener("tick", () => {
      refresh().catch(() => {});
      if (detail && !dirtyRef.current && !busy)
        api(`/images/${detail.id}`)
          .then((p) => {
            if (!active) return;
            setDetail((current) => (current?.id === p.id ? p : current));
            setLayout((current) => (dirtyRef.current ? current : p.layout));
          })
          .catch(() => {});
    });
    return () => {
      active = false;
      stream.close();
    };
  }, [batchId, detail?.id, busy]);
  const perform = async (name: string, action: () => Promise<void>) => {
    setBusy(name);
    setMessage("");
    try {
      await action();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy("");
    }
  };
  const choose = async (p: Picture) => {
    if (dirty && !confirm("当前修改尚未保存，是否放弃并切换？")) return;
    await perform("加载图片", async () => {
      const value = await api(`/images/${p.id}`);
      setDetail(value);
      setLayout(value.layout || null);
      setDirty(false);
      setSelected("");
      setVersion("");
    });
  };
  const changed = (l: Layout) => {
    setLayout(l);
    setDirty(true);
  };
  const save = async (confirmResult = false) => {
    if (!detail || !layout) return;
    if (
      confirmResult &&
      !confirm(
        "请确认已逐一检查所有面板、文字、图形和共享标题。确认后将允许正式导出；OCR 或模型提示不能代替你的判断。",
      )
    )
      return;
    await perform(confirmResult ? "确认并检查" : "保存并试切", async () => {
      const p = await api(`/images/${detail.id}/layout`, "POST", {
        layout,
        revision: detail.revision,
        confirm: confirmResult,
        acknowledge_warnings: confirmResult,
        ocr: confirmResult,
      });
      setDetail(p);
      setLayout(p.layout);
      setDirty(false);
      await refresh();
      notify(
        confirmResult
          ? "已人工确认，正式导出将包含这张图"
          : "草稿已保存，裁切预览已更新",
      );
    });
  };
  const current = layout
    ? [...layout.panels, ...layout.shared_regions].find(
        (r) => r.id === selected,
      )
    : undefined;
  const shared = !!layout?.shared_regions.some((r) => r.id === selected);
  const updateRegion = (patch: Partial<Region>) => {
    if (!layout || !current) return;
    const l = clone(layout);
    Object.assign(
      [...l.panels, ...l.shared_regions].find((r) => r.id === selected)!,
      patch,
    );
    changed(l);
  };
  const visible = pictures.filter(
    (p) =>
      (filter === "all" ||
        (filter === "review"
          ? ["NEEDS_REVIEW", "FAILED", "INTERRUPTED"].includes(p.status)
          : ["AUTO_ACCEPTED", "CONFIRMED"].includes(p.status))) &&
      p.name.toLowerCase().includes(search.toLowerCase()),
  );
  const done = pictures.filter((p) =>
    ["AUTO_ACCEPTED", "CONFIRMED"].includes(p.status),
  ).length;
  return (
    <>
      <header className="app-header">
        <div className="wordmark">
          <span className="mark">▧</span>
          <div>
            <strong>子图工作台</strong>
            <span>Subgraph Partitioning · ReAct</span>
          </div>
        </div>
        <div className="header-actions">
          <span className={"service " + (healthy ? "online" : "")}>
            {healthy === null
              ? "正在连接"
              : healthy
                ? "Agent 已连接"
                : "Agent 未连接"}
          </span>
          <button onClick={() => setShowProviders(true)}>模型配置</button>
          <a href="/api/gold" download="gold.json">
            标准答案
          </a>
        </div>
      </header>
      <main>
        <section className="page-heading">
          <div>
            <h1>让每张子图保持完整</h1>
            <p>原图分析、试切与返修，在同一处完成复核。</p>
          </div>
          <button
            className="primary"
            disabled={!!busy}
            onClick={() => fileInput.current?.click()}
          >
            ＋ 上传原图
          </button>
          <input
            ref={fileInput}
            type="file"
            accept="image/png,image/jpeg"
            multiple
            hidden
            onChange={(e) => {
              const files = Array.from(e.target.files || []);
              if (!files.length) return;
              perform("上传原图", async () => {
                let id = batchId;
                if (!id) {
                  const b = await api("/batches", "POST", {
                    name: `图组 ${new Date().toLocaleDateString()}`,
                  });
                  id = b.id;
                  setBatches((bs) => [b, ...bs]);
                  setBatchId(id);
                }
                for (let start = 0; start < files.length; start += 8) {
                  const data = new FormData();
                  files
                    .slice(start, start + 8)
                    .forEach((f) => data.append("files", f));
                  await api(`/batches/${id}/images`, "POST", data);
                  setMessage(
                    `已上传 ${Math.min(start + 8, files.length)} / ${files.length} 张`,
                  );
                }
                await refresh(id);
                notify(
                  `已上传 ${files.length} 张原图，可开始标注或 Agent 切分`,
                );
              });
              e.target.value = "";
            }}
          />
        </section>
        <section className="batch-toolbar">
          <label className="batch-picker">
            <span>当前批次</span>
            <select
              aria-label="当前批次"
              value={batchId}
              disabled={dirty || !!busy}
              onChange={(e) => setBatchId(e.target.value)}
            >
              <option value="">选择或新建批次</option>
              {batches.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </label>
          <button
            disabled={!!busy || dirty}
            onClick={() => {
              const name = prompt("批次名称");
              if (name)
                perform("新建批次", async () => {
                  const b = await api("/batches", "POST", { name });
                  setBatches((bs) => [b, ...bs]);
                  setBatchId(b.id);
                });
            }}
          >
            新建批次
          </button>
          <span className="toolbar-divider" />
          <select
            aria-label="本批次使用模型"
            value={providerId}
            onChange={(e) => setProviderId(e.target.value)}
          >
            <option value="">选择已验证模型</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id} disabled={!p.verified}>
                {p.name}
                {p.verified ? "" : " · 未验证"}
              </option>
            ))}
          </select>
          <button
            className="primary"
            disabled={
              !batchId || !providerId || !pictures.length || !!busy || dirty
            }
            onClick={() =>
              perform("启动批次", async () => {
                await api(`/batches/${batchId}/start`, "POST", {
                  provider_id: providerId,
                });
                await refresh();
                notify("批次已排队；默认并发 2 张，困难结果进入人工复核");
              })
            }
          >
            开始切分
          </button>
          <button
            disabled={!batchId || !!busy}
            onClick={() =>
              perform("取消任务", async () => {
                await api(`/batches/${batchId}/cancel`, "POST", {});
                await refresh();
              })
            }
          >
            取消任务
          </button>
          <div className="export-actions">
            <a
              className={!batchId ? "disabled" : ""}
              href={batchId ? `/api/batches/${batchId}/export` : "#"}
            >
              导出正式结果
            </a>
            {batchId && (
              <a href={`/api/batches/${batchId}/export?drafts=true`}>含草稿</a>
            )}
          </div>
        </section>
        {(message || busy) && (
          <div role="status" className="notice">
            <span>
              {busy ? `${busy}… ` : ""}
              {message}
            </span>
            <button aria-label="关闭提示" onClick={() => setMessage("")}>
              ✕
            </button>
          </div>
        )}
        {!pictures.length ? (
          <section className="empty">
            <div className="empty-symbol">A │ B</div>
            <h2>从一张复合图开始</h2>
            <p>
              上传 PNG 或 JPEG。可以先手动标注，也可以配置视觉模型，让 Agent
              观察原图、调用工具、试切并复核。
            </p>
            <button onClick={() => fileInput.current?.click()}>选择原图</button>
            <span>原图与既有切分结果会完整保留。</span>
          </section>
        ) : (
          <div className="workbench">
            <aside className="image-list">
              <div className="list-heading">
                <h2>
                  原图 <span>{pictures.length}</span>
                </h2>
                <small>{done} 张已通过</small>
              </div>
              <input
                aria-label="搜索原图"
                placeholder="搜索文件名"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <select
                aria-label="筛选状态"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              >
                <option value="all">全部状态</option>
                <option value="review">需要处理</option>
                <option value="accepted">已通过</option>
              </select>
              <div className="image-rows">
                {visible.map((p) => (
                  <button
                    key={p.id}
                    disabled={!!busy}
                    className={
                      "image-row " + (detail?.id === p.id ? "selected" : "")
                    }
                    onClick={() => choose(p)}
                  >
                    <span>{p.name}</span>
                    <small className={"status " + p.status}>
                      {names[p.status] || p.status}
                    </small>
                  </button>
                ))}
                {!visible.length && <p>没有符合条件的图片。</p>}
              </div>
            </aside>
            <section className="workspace">
              {!detail ? (
                <div className="select-empty">
                  <h2>选择左侧原图</h2>
                  <p>检查 Agent 结果，或开始建立人工标准答案。</p>
                </div>
              ) : (
                <>
                  <div className="image-toolbar">
                    <div>
                      <strong>{detail.name}</strong>
                      <span className={"status " + detail.status}>
                        {names[detail.status] || detail.status}
                      </span>
                      <small>
                        版本 {detail.revision}
                        {dirty ? " · 有未保存修改" : ""}
                      </small>
                    </div>
                    <div>
                      <button
                        disabled={!!busy || locked(detail)}
                        onClick={() =>
                          perform("导入旧结果", async () => {
                            if (
                              dirty &&
                              !confirm("导入将替换尚未保存的布局，继续？")
                            )
                              return;
                            const p = await api(
                              `/images/${detail.id}/legacy`,
                              "POST",
                              { revision: detail.revision },
                            );
                            setDetail(p);
                            setLayout(p.layout);
                            setDirty(false);
                            notify("旧结果已导入为草稿，请逐面板检查后再确认");
                          })
                        }
                      >
                        旧结果作草稿
                      </button>
                      <button
                        disabled={!!busy || locked(detail) || !layout}
                        onClick={() => save(false)}
                      >
                        保存并试切
                      </button>
                      <button
                        className="primary"
                        disabled={
                          !!busy || locked(detail) || !layout?.panels.length
                        }
                        onClick={() => save(true)}
                      >
                        人工确认
                      </button>
                    </div>
                  </div>
                  {detail.error && (
                    <p role="alert" className="error-line">
                      {detail.error}
                    </p>
                  )}
                  {layout ? (
                    <Editor
                      picture={detail}
                      layout={layout}
                      onChange={changed}
                      selected={selected}
                      setSelected={setSelected}
                      mode={mode}
                      setMode={setMode}
                    />
                  ) : (
                    <p>图片未成功解码，请检查文件和服务。</p>
                  )}
                  {(detail.versions?.length || 0) > 0 && (
                    <details className="versions">
                      <summary>裁切预览与版本对照</summary>
                      <label>
                        对照版本
                        <select
                          value={version}
                          onChange={(e) => setVersion(e.target.value)}
                        >
                          <option value="">选择历史版本</option>
                          {detail.versions?.map((v) => (
                            <option key={v.revision} value={v.revision}>
                              版本 {v.revision} · {names[v.status]}
                            </option>
                          ))}
                        </select>
                      </label>
                      <div className="compare">
                        {version && (
                          <figure>
                            <img
                              src={asset(
                                detail.id,
                                "preview.png",
                                Number(version),
                              )}
                              alt={`历史版本 ${version} 边界预览`}
                            />
                            <figcaption>历史版本 {version}</figcaption>
                          </figure>
                        )}
                        <figure>
                          <img
                            src={asset(detail.id, "preview.png")}
                            alt="当前已保存的裁切边界"
                          />
                          <figcaption>
                            当前已保存版本 {detail.revision}
                            {dirty ? "（不含未保存修改）" : ""}
                          </figcaption>
                        </figure>
                      </div>
                    </details>
                  )}
                </>
              )}
            </section>
            <aside className="inspector">
              <h2>面板与检查</h2>
              {detail && layout ? (
                <>
                  <div className="region-tabs">
                    {[...layout.panels, ...layout.shared_regions].map((r) => (
                      <button
                        key={r.id}
                        className={r.id === selected ? "chosen" : ""}
                        onClick={() => setSelected(r.id)}
                      >
                        {r.label}
                        {layout.shared_regions.includes(r) ? " · 共享" : ""}
                      </button>
                    ))}
                  </div>
                  {current ? (
                    <fieldset disabled={locked(detail) || !!busy}>
                      <legend>{shared ? "共享区域" : "面板"}属性</legend>
                      <label>
                        标签
                        <input
                          value={current.label}
                          onChange={(e) =>
                            updateRegion({ label: e.target.value })
                          }
                        />
                      </label>
                      <div className="coords">
                        {["x1", "y1", "x2", "y2"].map((name, i) => (
                          <label key={name}>
                            {name}
                            <input
                              type="number"
                              value={current.rect[i]}
                              onChange={(e) => {
                                const rect = [
                                  ...current.rect,
                                ] as Region["rect"];
                                rect[i] = Math.round(Number(e.target.value));
                                updateRegion({ rect });
                              }}
                            />
                          </label>
                        ))}
                      </div>
                      {shared && (
                        <div className="associations">
                          <p>关联面板（至少一个）</p>
                          {layout.panels.map((p) => (
                            <label key={p.id}>
                              <input
                                type="checkbox"
                                checked={
                                  current.panel_ids?.includes(p.id) || false
                                }
                                onChange={(e) =>
                                  updateRegion({
                                    panel_ids: e.target.checked
                                      ? [...(current.panel_ids || []), p.id]
                                      : (current.panel_ids || []).filter(
                                          (id) => id !== p.id,
                                        ),
                                  })
                                }
                              />
                              {p.label}
                            </label>
                          ))}
                        </div>
                      )}
                      <button
                        className="danger"
                        onClick={() => {
                          const l = clone(layout);
                          l.panels = l.panels.filter((r) => r.id !== selected);
                          l.shared_regions = l.shared_regions
                            .filter((r) => r.id !== selected)
                            .map((r) => ({
                              ...r,
                              panel_ids: r.panel_ids.filter(
                                (id) => id !== selected,
                              ),
                            }));
                          changed(l);
                          setSelected("");
                        }}
                      >
                        删除此区域
                      </button>
                      {detail.revision > 0 && !dirty && (
                        <img
                          className="crop-preview"
                          src={asset(
                            detail.id,
                            `${shared ? "shared" : "panel"}_${current.id}.png`,
                          )}
                          alt={`${current.label} 实际裁切结果`}
                          onError={(e) => {
                            e.currentTarget.style.display = "none";
                          }}
                        />
                      )}
                    </fieldset>
                  ) : (
                    <p className="hint">
                      选择裁框查看精确坐标，或在画布工具栏添加面板、共享标题。
                    </p>
                  )}
                  <section className="issues">
                    <h3>检查记录</h3>
                    {dirty ? (
                      <p>有未保存修改。保存并试切后更新检查结果。</p>
                    ) : detail.issues?.length ? (
                      detail.issues.map((i, n) => (
                        <div className="issue" key={n}>
                          <strong>{i.code}</strong>
                          <p>{i.message}</p>
                        </div>
                      ))
                    ) : (
                      <p>暂无检查问题记录。尚未检查不代表结果正确。</p>
                    )}
                  </section>
                  <section>
                    <h3>
                      执行记录{detail.calls ? ` · ${detail.calls} 次调用` : ""}
                    </h3>
                    <ol className="events">
                      {detail.events?.slice(-8).map((e, i) => (
                        <li key={i}>
                          {e.message}
                          <small>{e.action}</small>
                        </li>
                      ))}
                    </ol>
                    {[
                      "NEEDS_REVIEW",
                      "FAILED",
                      "INTERRUPTED",
                      "CANCELLED",
                    ].includes(detail.status) && (
                      <button
                        disabled={!!busy || dirty}
                        onClick={() => {
                          const feedback = prompt(
                            "给 Agent 的修改意见",
                            "请重新检查文字、图形与邻接面板边界",
                          );
                          if (feedback)
                            perform("恢复任务", async () => {
                              await api(`/images/${detail.id}/retry`, "POST", {
                                feedback,
                              });
                              await refresh();
                            });
                        }}
                      >
                        附意见继续 Agent
                      </button>
                    )}
                  </section>
                  <button
                    disabled={detail.status !== "CONFIRMED" || dirty || !!busy}
                    onClick={() =>
                      perform("保存标准答案", async () => {
                        await api(`/images/${detail.id}/gold`, "POST", {
                          notes: "人工逐图确认",
                        });
                        notify("已保存到标准答案集，可从页头导出");
                      })
                    }
                  >
                    存为人工标准答案
                  </button>
                </>
              ) : (
                <p className="hint">选中原图后显示面板、裁框属性与检查记录。</p>
              )}
            </aside>
          </div>
        )}
      </main>
      <footer className="app-footer">
        <span>本机工作台 · 原图只读 · 正式导出仅包含通过结果</span>
        <span>共享标题单独保留并关联面板</span>
      </footer>
      {showProviders && (
        <Providers
          providers={providers}
          reload={reloadProviders}
          onClose={() => setShowProviders(false)}
          notify={notify}
        />
      )}
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
