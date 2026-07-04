import { defineComponent } from 'vue'

// 保留左侧导航路由，但不保留引流工作台功能实现。
export default defineComponent({
  name: 'TrafficPlaceholderPage',
  setup() {
    return () => null
  },
})
