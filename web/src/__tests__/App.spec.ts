import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import App from '@/App.vue'
import router from '@/router'

describe('App', () => {
  it('renders the brand and the home view', async () => {
    router.push('/')
    await router.isReady()
    const wrapper = mount(App, { global: { plugins: [router] } })
    expect(wrapper.text()).toContain('RepoAegis')
    expect(wrapper.text()).toContain('任务')
  })
})
