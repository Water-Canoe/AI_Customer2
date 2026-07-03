import { h, type Component, type VNodeChild } from 'vue'

export type WorkbenchTone = 'teal' | 'blue' | 'green' | 'amber' | 'red' | 'purple' | 'gray'

type Child = VNodeChild | VNodeChild[] | null | undefined

// Shared render helpers keep page headers, metrics and empty states consistent.
function childList(child: Child) {
  if (Array.isArray(child)) return child
  return child === null || child === undefined ? [] : [child]
}

export function iconBadge(icon: Component, tone: WorkbenchTone = 'teal') {
  return h('span', { class: ['ui-icon-badge', `tone-${tone}`] }, [h(icon)])
}

export function sectionTitle(args: {
  title: string
  subtitle?: string
  icon: Component
  tone?: WorkbenchTone
  aside?: Child
  compact?: boolean
}) {
  return h('div', { class: ['section-title', 'section-title-with-icon', args.compact ? 'compact' : ''] }, [
    h('div', { class: 'section-title-main' }, [
      iconBadge(args.icon, args.tone || 'teal'),
      h('div', [
        h('h2', args.title),
        args.subtitle ? h('span', args.subtitle) : null,
      ]),
    ]),
    args.aside ? h('div', { class: 'section-title-aside' }, childList(args.aside)) : null,
  ])
}

export function pageAction(args: {
  title: string
  description: string
  icon: Component
  tone?: WorkbenchTone
  aside?: Child
}) {
  return h('section', { class: ['page-action', `tone-${args.tone || 'teal'}`] }, [
    iconBadge(args.icon, args.tone || 'teal'),
    h('div', { class: 'page-action-body' }, [
      h('strong', args.title),
      h('p', args.description),
    ]),
    args.aside ? h('div', { class: 'page-action-aside' }, childList(args.aside)) : null,
  ])
}

export function emptyState(args: {
  title: string
  description: string
  icon: Component
  tone?: WorkbenchTone
  action?: Child
}) {
  return h('div', { class: ['empty-state ui-empty-state', `tone-${args.tone || 'gray'}`] }, [
    iconBadge(args.icon, args.tone || 'gray'),
    h('strong', args.title),
    h('p', args.description),
    ...childList(args.action),
  ])
}

export function metricTile(args: {
  label: string
  value: string | number
  icon: Component
  tone?: WorkbenchTone
}) {
  return h('div', { class: ['metric-tile', `tone-${args.tone || 'gray'}`] }, [
    h('div', { class: 'metric-tile-head' }, [
      iconBadge(args.icon, args.tone || 'gray'),
      h('small', args.label),
    ]),
    h('strong', String(args.value)),
  ])
}
