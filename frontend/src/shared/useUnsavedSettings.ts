import { ElMessageBox } from 'element-plus'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'

// 设置只在用户保存时生效；离开前允许明确放弃输入，不自动提交凭据或运行规则。
export function useUnsavedSettings(hasChanges: () => boolean) {
  async function guard() {
    if (!hasChanges()) return true
    try {
      await ElMessageBox.confirm('还有未保存的设置，离开会丢弃这些输入。', '离开设置', {
        confirmButtonText: '放弃修改并离开', cancelButtonText: '继续编辑', type: 'warning',
      })
      return true
    } catch { return false }
  }
  onBeforeRouteLeave(guard)
  onBeforeRouteUpdate(guard)
}
