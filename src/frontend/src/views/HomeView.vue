<script setup lang="ts">
import { RouterLink } from 'vue-router'
</script>

<template>
  <div class="home-page">
    <div class="hero">
      <h1>🧬 Plasmid Designer</h1>
      <p class="hero-subtitle">自动化质粒构建设计平台</p>
      <p class="hero-description">
        从氨基酸或DNA序列开始，自动完成密码子优化、引物设计、克隆策略生成
      </p>
      <RouterLink to="/design" class="btn btn-primary hero-btn">
        开始设计
      </RouterLink>
    </div>
    
    <div class="features">
      <div class="feature-card">
        <div class="feature-icon">⚡</div>
        <h3>密码子优化</h3>
        <p>多物种优化算法，CAI最大化，GC平衡</p>
      </div>
      
      <div class="feature-card">
        <div class="feature-icon">🧪</div>
        <h3>多种克隆方法</h3>
        <p>支持 Gibson Assembly、Golden Gate、限制性酶切克隆</p>
      </div>
      
      <div class="feature-card">
        <div class="feature-icon">📊</div>
        <h3>自动引物设计</h3>
        <p>基于 Primer3 算法，自动设计高质量引物</p>
      </div>
      
      <div class="feature-card">
        <div class="feature-icon">📄</div>
        <h3>完整输出</h3>
        <p>GenBank 文件、引物订单表、实验方案一站式生成</p>
      </div>
    </div>
    
    <div class="workflow">
      <h2>工作流程</h2>
      <div class="workflow-steps">
        <div class="step">
          <div class="step-number">1</div>
          <div class="step-content">
            <h4>输入序列</h4>
            <p>上传氨基酸序列或DNA序列</p>
          </div>
        </div>
        
        <div class="step-arrow">→</div>
        
        <div class="step">
          <div class="step-number">2</div>
          <div class="step-content">
            <h4>选择载体和方法</h4>
            <p>选择目标载体和克隆方法</p>
          </div>
        </div>
        
        <div class="step-arrow">→</div>
        
        <div class="step">
          <div class="step-number">3</div>
          <div class="step-content">
            <h4>自动设计</h4>
            <p>系统自动优化和设计</p>
          </div>
        </div>
        
        <div class="step-arrow">→</div>
        
        <div class="step">
          <div class="step-number">4</div>
          <div class="step-content">
            <h4>下载结果</h4>
            <p>获取完整设计方案</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home-page {
  max-width: 1000px;
  margin: 0 auto;
}

.hero {
  text-align: center;
  padding: 4rem 2rem;
  background: white;
  border-radius: 1rem;
  margin-bottom: 3rem;
  box-shadow: var(--shadow);
}

.hero h1 {
  font-size: 2.5rem;
  margin-bottom: 0.5rem;
}

.hero-subtitle {
  font-size: 1.25rem;
  color: var(--primary-color);
  margin-bottom: 1rem;
}

.hero-description {
  color: var(--text-secondary);
  margin-bottom: 2rem;
  max-width: 600px;
  margin-left: auto;
  margin-right: auto;
}

.hero-btn {
  font-size: 1.125rem;
  padding: 1rem 2rem;
}

.features {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 1.5rem;
  margin-bottom: 3rem;
}

.feature-card {
  background: white;
  padding: 2rem;
  border-radius: 0.75rem;
  box-shadow: var(--shadow);
}

.feature-icon {
  font-size: 2.5rem;
  margin-bottom: 1rem;
}

.feature-card h3 {
  font-size: 1.125rem;
  margin-bottom: 0.5rem;
}

.feature-card p {
  color: var(--text-secondary);
  font-size: 0.875rem;
}

.workflow {
  background: white;
  padding: 2rem;
  border-radius: 0.75rem;
  box-shadow: var(--shadow);
}

.workflow h2 {
  text-align: center;
  margin-bottom: 2rem;
}

.workflow-steps {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 1rem;
}

.step {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  max-width: 150px;
}

.step-number {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: var(--primary-color);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  margin-bottom: 0.75rem;
}

.step-content h4 {
  font-size: 0.875rem;
  margin-bottom: 0.25rem;
}

.step-content p {
  font-size: 0.75rem;
  color: var(--text-secondary);
}

.step-arrow {
  font-size: 1.5rem;
  color: var(--text-secondary);
}
</style>
