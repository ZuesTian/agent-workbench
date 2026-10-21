export async function api(path, body) {
  const options = { credentials: 'same-origin', headers: { 'X-Workbench': '1' } };
  if (body !== undefined) {
    options.method = 'POST';
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch('/api/' + path, options);
  } catch {
    throw Error(
      body === undefined
        ? '无法连接本机服务，请从桌面入口重新打开。'
        : '与本机服务的连接中断。请先查看备份记录确认结果，再重试操作。',
    );
  }
  const value = await response.json();
  if (!response.ok) throw Error(value.error || '操作失败');
  return value;
}
