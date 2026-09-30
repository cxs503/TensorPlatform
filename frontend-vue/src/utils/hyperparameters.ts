export type HyperparameterMode = 'default' | 'detected' | 'manual' | 'file'

const ALLOWED_SUFFIXES = ['.json', '.txt', '.yaml', '.yml', '.cfg', '.conf']
const MAX_FILE_BYTES = 1024 * 1024

function parseScalar(raw: string): unknown {
  const value = raw.trim()
  if (!value) return ''
  try {
    return JSON.parse(value)
  } catch {
    return value.replace(/^(['"])(.*)\1$/, '$2')
  }
}

export function parseHyperparameterText(raw: string): Record<string, unknown> {
  const text = String(raw || '').trim()
  if (!text) return {}

  try {
    const parsed = JSON.parse(text)
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error('超参数必须是对象')
    }
    return parsed as Record<string, unknown>
  } catch (error) {
    if (text.startsWith('{') || text.startsWith('[')) {
      throw new Error(`JSON格式错误：${(error as Error).message}`)
    }
  }

  const result: Record<string, unknown> = {}
  for (const [index, original] of text.split(/\r?\n/).entries()) {
    const line = original.trim()
    if (!line || line.startsWith('#') || line.startsWith('//')) continue
    const separator = line.includes('=') ? line.indexOf('=') : line.indexOf(':')
    if (separator <= 0) throw new Error(`第${index + 1}行应使用 key=value 或 key: value`)
    const key = line.slice(0, separator).trim()
    if (!/^[A-Za-z_][A-Za-z0-9_.-]*$/.test(key)) {
      throw new Error(`第${index + 1}行参数名无效：${key}`)
    }
    result[key] = parseScalar(line.slice(separator + 1))
  }
  if (!Object.keys(result).length) throw new Error('参数文件中没有可用参数')
  return result
}

export async function readHyperparameterFile(file: File): Promise<{ params: Record<string, unknown>; text: string }> {
  const lowerName = file.name.toLowerCase()
  if (!ALLOWED_SUFFIXES.some(suffix => lowerName.endsWith(suffix))) {
    throw new Error(`仅支持 ${ALLOWED_SUFFIXES.join('、')} 参数文件`)
  }
  if (!file.size) throw new Error('参数文件为空')
  if (file.size > MAX_FILE_BYTES) throw new Error('参数文件不能超过1MB')
  const params = parseHyperparameterText(await file.text())
  return { params, text: JSON.stringify(params, null, 2) }
}

export function serializeHyperparameters(mode: HyperparameterMode, text: string): string {
  if (mode === 'default') return ''
  if (!String(text || '').trim()) {
    throw new Error(mode === 'file' ? '请上传参数文件' : '请输入超参数；如需使用默认值请选择“使用默认值”')
  }
  return JSON.stringify(parseHyperparameterText(text))
}
