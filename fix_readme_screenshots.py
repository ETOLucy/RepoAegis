import os, re

repo_dir = r'D:\Repos\Agents\RepoAegis'

screenshot_section_cn = '''

## 前端看板

RepoAegis 提供基于 Web 的前端看板，支持流水线监控、任务管理和评测结果查看：

| 模块 | 截图 |
|------|------|
| 流水线监控 | ![流水线监控](docs/assets/screenshot-pipelineview.png) |
| 任务控制台 | ![任务控制台](docs/assets/screenshot-tasksview.png) |
| 评测看板 | ![评测看板](docs/assets/screenshot-evalview.png) |
'''

screenshot_section_en = '''

## Frontend Dashboard

RepoAegis provides a web-based frontend dashboard for pipeline monitoring, task management, and evaluation results:

| Module | Screenshot |
|--------|-----------|
| Pipeline Monitor | ![Pipeline Monitor](docs/assets/screenshot-pipelineview.png) |
| Task Console | ![Task Console](docs/assets/screenshot-tasksview.png) |
| Evaluation Dashboard | ![Evaluation Dashboard](docs/assets/screenshot-evalview.png) |
'''

for filename, section in [('README.md', screenshot_section_cn), ('README-EN.md', screenshot_section_en)]:
    filepath = os.path.join(repo_dir, filename)
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find the mermaid block
    mermaid_start = content.find('`mermaid')
    if mermaid_start == -1:
        print(f'Warning: Could not find `mermaid in {filename}')
        continue

    search_start = mermaid_start + len('`mermaid')
    close_pos = content.find('`', search_start)
    if close_pos == -1:
        print(f'Warning: Could not find closing ` for mermaid in {filename}')
        continue

    insert_pos = close_pos + 3

    new_content = content[:insert_pos] + section + content[insert_pos:]

    with open(filepath, 'w', encoding='utf-8', newline='') as f:
        f.write(new_content)

    print(f'Updated {filename}: inserted screenshot section after mermaid diagram')

print('Done')
