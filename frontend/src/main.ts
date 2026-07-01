import { createApp } from 'vue'
import {
  ElAside,
  ElButton,
  ElContainer,
  ElHeader,
  ElIcon,
  ElMain,
  ElMenu,
  ElMenuItem,
  ElTag
} from 'element-plus'
import 'element-plus/dist/index.css'
import './styles.css'
import './workbench.css'
import App from './App.vue'
import { router } from './router'

const app = createApp(App)

;[ElAside, ElButton, ElContainer, ElHeader, ElIcon, ElMain, ElMenu, ElMenuItem, ElTag].forEach((component) => {
  app.component(component.name || '', component)
})

app.use(router)
app.mount('#app')
