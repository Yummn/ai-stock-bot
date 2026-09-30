export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api/v2${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const text = await response.text();
  let data: any;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(
      `服务暂时无法访问（${response.status}），已保留你的参数和结果。`,
    );
  }
  if (!response.ok)
    throw new Error(data.error || data.detail || `请求失败 ${response.status}`);
  return data;
}

export const clone = <T>(data: T): T => JSON.parse(JSON.stringify(data));
export const pct = (n: number | null | undefined) =>
  n == null ? "—" : `${(n * 100).toFixed(2)}%`;
export const money = (n: number | null | undefined) =>
  n == null
    ? "—"
    : Number(n).toLocaleString("zh-CN", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
export const dateTime = (n: number) =>
  new Date(n * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
export const sceneName = (value: string) =>
  ({ bull: "牛市 / 强势", bear: "熊市 / 弱势", sideways: "震荡 / 不确定" })[
    value
  ] || value;
export const statusName = (value: string) =>
  ({
    queued: "排队中",
    running: "运行中",
    done: "已完成",
    failed: "失败",
    stopped: "已停止",
    interrupted: "可继续",
  })[value] || value;
