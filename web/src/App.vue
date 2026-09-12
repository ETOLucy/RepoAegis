<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
import mark from '@/assets/mark.svg'
</script>

<template>
  <header class="topbar">
    <RouterLink to="/" class="brand">
      <img :src="mark" alt="" width="28" height="28" />
      <span>RepoAegis</span>
    </RouterLink>
  </header>
  <main class="content">
    <RouterView />
  </main>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  height: 3rem;
  padding: 0 1rem;
  border-bottom: 1px solid var(--color-border);
}
.brand {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 600;
  color: var(--color-heading);
  text-decoration: none;
}
.content {
  padding: 1.5rem 1rem;
}
</style>
