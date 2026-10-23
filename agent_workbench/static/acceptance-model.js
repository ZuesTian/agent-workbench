// Shared scheme validation; no DOM or execution dependencies.
export const checkKinds = { file: '非空文件', json: '有效 JSON', contains: '包含指定文本' };
export const acceptanceLabels = {
  not_configured: '未配置文件验收',
  pending: '等待文件验收',
  not_run: '未执行文件验收',
  passed: '文件验收通过',
  failed: '文件验收未通过',
};

export function acceptanceRules(value = []) {
  if (!Array.isArray(value) || value.length > 20) throw Error('最多配置 20 项文件验收');
  const result = value.map((raw) => {
    if (!raw || !Object.hasOwn(checkKinds, raw.kind)) throw Error('文件验收类型不正确');
    if (typeof raw.path !== 'string' || raw.path.length > 240)
      throw Error('交付路径最多 240 个字符');
    const path = raw.path.trim().replaceAll('\\', '/');
    if (
      !path ||
      /[<>:"|?*\x00-\x1f]/.test(path) ||
      path
        .split('/')
        .some(
          (part) =>
            !part ||
            ['.', '..'].includes(part) ||
            /[. ]$/.test(part) ||
            /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$/i.test(part),
        )
    )
      throw Error('请填写工作目录内的相对文件路径，不支持上级路径或特殊设备名');
    if (
      raw.expected !== undefined &&
      (typeof raw.expected !== 'string' || raw.expected.length > 1000)
    )
      throw Error('验收文本最多 1000 个字符');
    const expected = raw.kind === 'contains' ? (raw.expected || '').trim() : '';
    if (raw.kind === 'contains' && !expected) throw Error('请填写需要包含的文本');
    const changed = raw.changed ?? true;
    if (typeof changed !== 'boolean') throw Error('本次新增或变化选项必须是布尔值');
    return { path, kind: raw.kind, expected, changed };
  });
  if (result.reduce((sum, item) => sum + item.path.length + item.expected.length, 0) > 4000)
    throw Error('交付路径与验收文本合计最多 4000 个字符');
  return result;
}
