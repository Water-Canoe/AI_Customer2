import { defineComponent, h, ref } from 'vue'
import { Close } from '@element-plus/icons-vue'

export function splitTagText(text: string) {
  return text.split(/[\n\r,，;；]+/).map(item => item.trim()).filter(Boolean)
}

export function mergeTags(current: string[], incoming: string[]) {
  const next = [...current]
  incoming.forEach(item => {
    if (!next.includes(item)) next.push(item)
  })
  return next
}

export function joinTags(tags: string[]) {
  return tags.map(item => item.trim()).filter(Boolean).join(',')
}

export const TagInput = defineComponent({
  props: {
    modelValue: { type: Array, required: true },
    disabled: { type: Boolean, default: false },
    placeholder: { type: String, default: '' }
  },
  emits: ['update:modelValue', 'focus'],
  setup(props, { emit }) {
    const draft = ref('')
    const inputRef = ref<HTMLInputElement | null>(null)
    const currentTags = () => (props.modelValue as string[]).map(item => String(item))
    function updateTags(tags: string[]) {
      emit('update:modelValue', tags)
    }
    function commitDraft() {
      const incoming = splitTagText(draft.value)
      if (!incoming.length) return
      updateTags(mergeTags(currentTags(), incoming))
      draft.value = ''
    }
    function removeTag(index: number) {
      const next = currentTags()
      next.splice(index, 1)
      updateTags(next)
    }
    function onInput(event: Event) {
      const value = (event.target as HTMLInputElement).value
      if (/[\n\r,，;；]/.test(value)) {
        updateTags(mergeTags(currentTags(), splitTagText(value)))
        draft.value = ''
        return
      }
      draft.value = value
    }
    function onPaste(event: ClipboardEvent) {
      const text = event.clipboardData?.getData('text') || ''
      if (!/[\n\r,，;；]/.test(text)) return
      event.preventDefault()
      updateTags(mergeTags(currentTags(), splitTagText(text)))
      draft.value = ''
    }
    function onKeydown(event: KeyboardEvent) {
      if (event.key === 'Enter') {
        event.preventDefault()
        commitDraft()
      }
      if (event.key === 'Backspace' && !draft.value && currentTags().length) {
        removeTag(currentTags().length - 1)
      }
    }
    return () => {
      const tags = currentTags()
      return h('div', {
        class: ['tag-input', props.disabled ? 'disabled' : ''],
        onClick: () => inputRef.value?.focus()
      }, [
        ...tags.map((tag, index) => h('span', { class: 'tag-chip' }, [
          h('span', { class: 'tag-chip-text' }, tag),
          h('button', {
            type: 'button',
            class: 'tag-remove',
            title: `移除 ${tag}`,
            disabled: props.disabled,
            onClick: (event: MouseEvent) => {
              event.stopPropagation()
              removeTag(index)
            }
          }, [h(Close, { class: 'tag-remove-icon' })])
        ])),
        h('input', {
          ref: inputRef,
          value: draft.value,
          disabled: props.disabled,
          placeholder: tags.length ? '' : props.placeholder,
          onFocus: () => emit('focus'),
          onInput,
          onPaste,
          onKeydown,
          onBlur: commitDraft
        })
      ])
    }
  }
})

const icpFields = [
  { key: 'product', label: '产品/服务', placeholder: '例如：AI客服、获客工具' },
  { key: 'industry', label: '目标行业', placeholder: '例如：跨境电商、教育培训' },
  { key: 'roles', label: '目标角色', placeholder: '例如：老板、运营负责人、销售主管' },
  { key: 'pain_points', label: '典型痛点', placeholder: '用户常见问题、需求或抱怨', multiline: true },
  { key: 'high_intent_words', label: '高意向词', placeholder: '例如：求推荐、怎么选、多少钱', multiline: true },
  { key: 'value_proposition', label: '价值主张', placeholder: '产品能解决什么问题，适合什么客户', multiline: true },
  { key: 'excluded_audience', label: '排除人群', placeholder: '不需要跟进的人群或场景', multiline: true }
]
