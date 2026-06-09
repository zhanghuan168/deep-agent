import axios from 'axios'
import { ElMessage } from 'element-plus'

const client = axios.create({
  baseURL: '/api',
  timeout: 60000,
})

client.interceptors.response.use(
  (r) => r.data,
  (err) => {
    const msg = err.response?.data?.error || err.message
    ElMessage.error('API 错误: ' + msg)
    return Promise.reject(new Error(msg))
  }
)

export const api = (path, opts = {}) => {
  const { method = 'GET', body, params } = opts
  return client.request({
    url: path,
    method,
    data: body,
    params,
  })
}

export default client
