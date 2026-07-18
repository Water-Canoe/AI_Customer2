import { defineComponent, h } from 'vue'

import { RuntimeQueuePanel } from '../components/runtime/RuntimeQueuePanel'


export default defineComponent({
  name: 'RuntimeCenterPage',
  props: { refreshSeq: { type: Number, default: 0 } },
  setup(props) {
    return () => h('div', { class: 'runtime-center-page' }, [
      h(RuntimeQueuePanel, { refreshSeq: props.refreshSeq }),
    ])
  },
})
