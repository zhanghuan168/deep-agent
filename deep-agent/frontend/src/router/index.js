import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/board' },
  { path: '/board', name: 'Board', component: () => import('@/views/BoardView.vue') },
  { path: '/detail/:id?', name: 'Detail', component: () => import('@/views/DetailView.vue') },
  { path: '/gantt', name: 'Gantt', component: () => import('@/views/GanttView.vue') },
  { path: '/review', name: 'Review', component: () => import('@/views/ReviewView.vue') },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
