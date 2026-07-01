import { defineComponent, h, onBeforeUnmount, reactive, ref } from 'vue'
import { clamp } from '../../shared/format'

const splitWidths = reactive<Record<string, number>>({})

export const SplitPane = defineComponent({
  props: {
    storageKey: { type: String, required: true },
    side: { type: String, default: 'right' },
    defaultSideWidth: { type: Number, default: 360 },
    minSideWidth: { type: Number, default: 240 },
    maxSideWidth: { type: Number, default: 680 }
  },
  setup(props, { slots }) {
    if (!splitWidths[props.storageKey]) splitWidths[props.storageKey] = props.defaultSideWidth
    const dragging = ref(false)
    let startX = 0
    let startWidth = 0

    function setWidth(nextWidth: number) {
      splitWidths[props.storageKey] = clamp(nextWidth, props.minSideWidth, props.maxSideWidth)
    }

    function stopDrag() {
      dragging.value = false
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', stopDrag)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }

    function onPointerMove(event: PointerEvent) {
      const deltaX = event.clientX - startX
      setWidth(props.side === 'left' ? startWidth + deltaX : startWidth - deltaX)
    }

    function startDrag(event: PointerEvent) {
      event.preventDefault()
      dragging.value = true
      startX = event.clientX
      startWidth = splitWidths[props.storageKey]
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
      window.addEventListener('pointermove', onPointerMove)
      window.addEventListener('pointerup', stopDrag)
    }

    function onKeydown(event: KeyboardEvent) {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return
      event.preventDefault()
      const direction = event.key === 'ArrowRight' ? 1 : -1
      const step = event.shiftKey ? 40 : 16
      setWidth(splitWidths[props.storageKey] + (props.side === 'left' ? direction * step : -direction * step))
    }

    onBeforeUnmount(stopDrag)

    return () => {
      const sideWidth = splitWidths[props.storageKey]
      const gridTemplateColumns = props.side === 'left'
        ? `${sideWidth}px 10px minmax(0, 1fr)`
        : `minmax(0, 1fr) 10px ${sideWidth}px`
      const mainSlot = slots.default?.() || []
      const sideSlot = slots.side?.() || []
      const resizer = h('button', {
        class: 'split-resizer',
        type: 'button',
        title: '拖动调整左右宽度',
        'aria-label': '拖动调整左右宽度',
        onPointerdown: startDrag,
        onKeydown
      })
      return h('div', { class: ['split-pane', `side-${props.side}`, dragging.value ? 'dragging' : ''], style: { gridTemplateColumns } },
        props.side === 'left' ? [...sideSlot, resizer, ...mainSlot] : [...mainSlot, resizer, ...sideSlot]
      )
    }
  }
})
