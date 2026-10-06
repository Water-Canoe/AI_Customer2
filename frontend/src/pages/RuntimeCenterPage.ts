import { defineComponent, h } from 'vue'
import { useRoute } from 'vue-router'

import { RuntimeQueuePanel } from '../components/runtime/RuntimeQueuePanel'


export default defineComponent({
  name: 'RuntimeCenterPage',
  props: { refreshSeq: { type: Number, default: 0 } },
  setup(props) {
    const route = useRoute()
    return () => h('div', { class: 'runtime-center-page' }, [
      h(RuntimeQueuePanel, { refreshSeq: props.refreshSeq, initialStatus: String(route.query.status || ''), initialKind: String(route.query.kind || ''), initialPage: Number(route.query.page || 1), initialPageSize: Number(route.query.page_size || 20), initialJobId: String(route.query.job || '') }),
    ])
  },
})
