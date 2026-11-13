"""Create the standalone illustrated HTML guide for the Windows trial."""

import base64
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGES = {
    "review": "materials-review-20261102/01-review-desktop.png",
    "preview": "materials-review-20261102/08-local-preview.png",
    "paper": "desktop-trial-20261102/materials-profile/example-page-1.png",
}

TEMPLATE = (
    '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" '
    'content="width=device-width,initial-scale=1"><title>RAG Workbench 试用操作教程</title>'
    '<style>\n'

    ':root{color-scheme:light;--ink:#292925;--muted:#6b6a63;--accent:#ba573b;--paper:#faf9f5}'
    '*{box-sizing:border-box}body{margin:0;background:#f2f0e9;color:var(--ink);font:16px/1.8 '
    '"Microsoft YaHei",sans-serif}a{color:#9d482f}header{background:#252824;color:#f9f7ef;'
    'padding:64px max(6vw,24px)}header p{color:#c9c8bd;max-width:760px}'
    'h1{font-size:clamp(30px,5vw,52px);line-height:1.2;margin:10px 0}h2{font-size:28px;margin:0 '
    '0 14px}h3{font-size:19px;margin-bottom:8px}.eyebrow{color:#e69c7f;letter-spacing:.13em;'
    'font-size:13px}.layout{max-width:1280px;margin:auto;display:grid;'
    'grid-template-columns:220px minmax(0,1fr);gap:36px;padding:38px 24px}.nav{position:sticky;'
    'top:24px;align-self:start}.nav a{display:block;text-decoration:none;color:#62635c;'
    'padding:5px 0}.nav a:hover{color:var(--accent)}main{min-width:0}'
    'section{background:var(--paper);border:1px solid #e3e0d7;border-radius:18px;padding:32px;'
    'margin-bottom:22px;box-shadow:0 8px 25px #30281a08}.num{color:var(--accent);font-size:13px;'
    'letter-spacing:.14em;font-weight:bold}.lead{font-size:18px}.note{background:#f6e9df;'
    'border-left:4px solid #c46745;padding:14px 18px;border-radius:5px}.flow{display:flex;'
    'flex-wrap:wrap;gap:9px;margin:22px 0}.flow span{background:#edece4;border-radius:8px;'
    "padding:8px 13px}.flow span:not(:last-child):after{content:' →';color:var(--accent)}"
    'figure{margin:24px 0}figure img{display:block;width:100%;height:auto;border:1px solid '
    '#dedbd2;border-radius:12px}figure.small img{max-width:600px;margin:auto}'
    'figcaption{color:var(--muted);font-size:13px;margin-top:8px}code{background:#eeece5;'
    'border-radius:4px;padding:2px 5px;overflow-wrap:anywhere}table{border-collapse:collapse;'
    'width:100%;font-size:15px}th,td{border-bottom:1px solid #e5e2d9;text-align:left;'
    'padding:9px;vertical-align:top}th{color:#6b635a}.tag{display:inline-block;color:#a24c32;'
    'background:#fae7dc;padding:3px 10px;border-radius:20px;font-size:13px}footer{color:#777;'
    'padding:10px 0 40px}@media(max-width:760px){.layout{display:block;padding:18px}'
    '.nav{position:static;margin-bottom:20px;display:flex;gap:12px;overflow:auto;'
    'white-space:nowrap}.nav a{font-size:14px}section{padding:22px}header{padding:42px 24px}'
    'table{display:block;overflow:auto}}@media print{.nav{display:none}.layout{display:block}'
    'section{break-inside:avoid;box-shadow:none}body{background:white}}\n'

    '</style></head><body><header><div class="eyebrow">RAG WORKBENCH · WINDOWS 试用版 '
    '{{version}}</div><h1>从一篇论文到<br>可核对的数据记录</h1><p>一份面向首次使用者的操作教程。'
    '跟着示例完成导入、原文定位、手工记录、证据核对与导出，再按需启用 AI 辅助提取。</p><span '
    'class="tag">支持离线阅读 · 图例数据均为虚构</span></header><div class="layout"><nav '
    'class="nav"><b>教程目录</b><a href="#start">01 安装与启动</a><a href="#map">02 '
    '认识工作区</a><a href="#import">03 导入文献</a><a href="#source">04 预览原文</a><a '
    'href="#manual">05 手工整理数据</a><a href="#evidence">06 关联证据与审核</a><a href="#ai">'
    '07 AI 辅助提取</a><a href="#export">08 导出与备份</a><a href="#faq">09 常见问题</a></nav>'
    '<main>\n'

    '<section id="start"><div class="num">01 / START</div><h2>安装并启动</h2><ol><li>将 <code>'
    'RAG-Workbench-Trial-{{version}}-win-x64.zip</code> 完整解压到可写目录，如 <code>'
    'D:\\Apps\\RAG-Workbench</code>。</li><li>打开解压得到的文件夹，双击 <b>RAG '
    'Workbench.exe</b>。首次启动需要准备本地模型，请稍等。</li><li>保持 EXE 与同目录的 <code>'
    'runtime</code>、<code>resources</code>、<code>models</code> 一起存放。无需安装 Python。'
    '</li></ol><div class="note">Windows 10/11 x64 需要 Microsoft Edge WebView2 Runtime 和 .NET '
    'Framework 4.6.2+。软件目录必须可写；程序首次启动会在其中创建 <code>user-data</code>。</div>'
    '</section>\n'

    '<section id="map"><div class="num">02 / OVERVIEW</div><h2>先认识工作区</h2><p>侧边栏中的 '
    '<b>材料文献</b>用于整理论文、性能记录与证据；<b>知识库管理</b>'
    '用于导入资料并建立聊天检索索引；<b>AI 聊天</b>用于提问。两处导入入口各自服务于不同工作流程'
    '。</p><div class="flow"><span>导入论文</span><span>查看原文</span><span>建立候选</span>'
    '<span>核对证据</span><span>批准并导出</span></div><figure><img src="{{review}}" '
    'alt="材料文献页面：左侧原文预览，右侧记录与证据"><figcaption>实际界面截图：'
    '左侧为页面与原文，右侧为记录、证据和编辑区。截图使用另一份虚构验收论文，'
    '名称和数值与试用包示例不同。</figcaption></figure></section>\n'

    '<section id="import"><div class="num">03 / IMPORT</div><h2>导入第一篇论文</h2><ol><li>'
    '点击左侧 <b>材料文献</b>，展开“导入论文与补充材料”。</li><li>在“选择 PDF、TXT 或 '
    'Markdown”中选择试用包 <code>试用示例/synthetic-ofet.pdf</code>。</li><li>“文件角色”选 <b>'
    '论文正文</b>；“解析方式”选 <b>文本层解析</b>，点击 <b>解析并保存原文</b>。</li><li>'
    '在“当前文献”下拉框选中已导入文件。若有补充材料，先导入正文，再选“补充材料”并指定所属论文。'
    '</li></ol><p>导入只保存在本地，不调用模型。文本层解析适用于带可选文字的 PDF；'
    '扫描图片不会自动 OCR。Docling 属于额外可选组件，试用包默认使用文本层解析。</p><div '
    'class="note">示例的材料名称、条件和性能数值全部虚构，仅供学习操作，'
    '不可作为真实论文、实验结果或预测训练数据。</div></section>\n'

    '<section id="source"><div class="num">04 / SOURCE</div><h2>浏览原文和页面</h2><p>'
    '在“提取与核对”或“原文证据”中查看文件，使用 <b>PDF 页码</b> 切换页面、使用 <b>页面缩放</b> '
    '调整大小。第 1 页列出示例性能，第 2 页给出器件、制备和测试上下文。</p><figure '
    'class="small"><img src="{{paper}}" alt="试用包中虚构 OFET 示例第一页"><figcaption>'
    '试用包内置示例第一页，页眉明确标注 SYNTHETIC DATA。</figcaption></figure><p>'
    '定位时以原文和实际页码为准；只有来源含真实坐标时才会显示对应定位框，'
    '不能把页面预览当作自动完成的图表识别。</p><figure><img src="{{preview}}" alt="本地 PDF '
    '原文预览界面"><figcaption>实际预览界面示意；示例为虚构测试资料。</figcaption></figure>'
    '</section>\n'

    '<section id="manual"><div class="num">05 / MANUAL</div><h2>无需 API，先做一条记录</h2><ol>'
    '<li>在“提取与核对”下找到 <b>手工添加候选（无需 API）</b> 并展开。</li><li>'
    '选择有机场效应晶体管模板，填写与原文一致的字段。材料名称、性能名称和原始数值为必填；'
    '未确认的信息留空。</li><li>展开 <b>关联原文证据（无需填写 JSON）</b>，'
    '按下一节步骤关联后点击 <b>保存新候选</b>。</li></ol><table><thead><tr><th>字段</th><th>'
    '示例原文值</th><th>查找位置</th></tr></thead><tbody><tr><td>材料名称</td><td>'
    'TEST-OSC-A</td><td>第 1 页</td></tr><tr><td>样品编号</td><td>SYN-01</td><td>第 1 页</td>'
    '</tr><tr><td>性能名称</td><td>mobility</td><td>第 1 页</td></tr><tr><td>原始数值 / '
    '单位</td><td>0.42 / cm^2 V^-1 s^-1</td><td>第 1 页</td></tr><tr><td>器件结构</td><td>Si / '
    'SiO2 / PMMA / TEST-OSC-A / Au</td><td>第 2 页</td></tr><tr><td>工艺 / 条件</td><td>100 C '
    'for 30 min in nitrogen / 300 K</td><td>第 2 页与第 1 页</td></tr></tbody></table><p>'
    '为方便证据校验，性能、条件和单位优先直接摘录原文，而不要把中文解释或多个不连续片段拼成原文'
    '字段。一个材料的不同性能可以分别建立多条记录。</p></section>\n'

    '<section id="evidence"><div class="num">06 / REVIEW</div><h2>给字段关联证据，再批准</h2>'
    '<ol><li>在“关联原文证据”中选择 <b>证据原文块</b>，确认页码与文字。</li><li>'
    '在“这段引文支持的字段”选择对应字段；引文框可以缩短，但必须与所选原文完全一致，'
    '并包含该字段值。</li><li>点击 <b>关联到字段证据</b>。不同页的字段分别选择原文块并重复此操作'
    '。</li><li>保存候选后，在“实验记录核对”选择记录，检查材料、数值、单位、工艺、测试条件和字段'
    '证据。每个已填写字段都需要相应证据，才能批准。</li><li>确认无误后使用界面上的批准操作。'
    '修改时离开输入框会保存本地草稿；草稿恢复提示不代表记录已经保存或批准。</li></ol><p>'
    '截图中的“查看字段证据”可以检查已关联的引文，“在原文中查看”用于回到对应来源。'
    '修订历史保留记录变更。</p></section>\n'

    '<section id="ai"><div class="num">07 / AI EXTRACTION</div><h2>使用 AI 辅助提取</h2><ol><li>'
    '窗口顶部菜单选择 <b>工作空间 → 配置 API Key</b>，在打开的试用版 <code>user-data/.env</code>'
    ' 中填写 <code>DEEPSEEK_API_KEY=你的Key</code>，保存并刷新或重开软件。</li><li>'
    '在“提取与核对”展开 <b>模型提取候选</b>，选择适合的“指标模板”，例如 OFET。</li><li>'
    '阅读页面显示的文献数量、原文块、字符量及预计请求次数；确认愿意将文字发送到所配置的模型接口'
    '并可能产生费用后勾选同意。</li><li>点击 <b>提取候选</b>，完成后逐条核对并补足证据，'
    '再决定批准或拒绝。</li></ol><div class="note">AI 返回的是待审核候选，可能遗漏或识别错误。'
    '正文提取会包含关联补充材料；图像、图表曲线与分子结构图片不会由文本提取自动读数。'
    '使用真实论文前应确认自己的文献及服务使用权限。</div></section>\n'

    '<section id="export"><div class="num">08 / EXPORT</div><h2>导出、保存与备份</h2><ol><li>'
    '选择当前文献，进入 <b>导出</b> 标签。检查页面的筛选条件；默认只导出已批准记录。</li><li>'
    '点击 <b>准备导出文件</b>，再选择 <b>下载 CSV</b> 或 <b>下载完整数据包</b>。CSV '
    '适合表格处理；ZIP 同时包含记录、证据、修订及关联原文。</li><li>关闭软件后，'
    '备份整个软件目录下的 <code>user-data</code>。该目录包含原文、数据、草稿及 API 配置，'
    '请妥善保管。</li></ol><p>向他人分发软件时使用原始试用 ZIP；不要把用过的 <code>'
    'user-data</code> 连同密钥和论文发出去。</p></section>\n'

    '<section id="faq"><div class="num">09 / HELP</div><h2>常见问题</h2><h3>没有 Key '
    '能试吗？</h3><p>可以导入、预览、手工录入、核对、批准、导出和使用本地检索。'
    '模型提取和生成聊天回答需要配置有效 Key、网络及相应服务余额。</p><h3>为什么批准不了？</h3>'
    '<p>检查每个已填字段是否有原文证据，摘录是否与所选原文块完全一致；没有证据的字段可留空，'
    '或补充对应证据后再批准。</p><h3>为什么没有预览文字或没有结果？</h3><p>确认 PDF 有文字层，'
    '扫描版需要另行 OCR。若是模型提取，请检查配置、远程发送确认、任务记录和文献文字是否覆盖需要'
    '的内容。</p><h3>能直接做性能预测或识别曲线吗？</h3><p>当前试用版尚未实现扫描件 '
    'OCR、图表曲线自动取点、分子结构图片识别或材料性能预测。</p><h3>启动失败怎么办？</h3><p>'
    '确认已完整解压到可写目录，检查 WebView2 Runtime；查看软件目录内 <code>'
    'user-data/desktop.log</code>，分享日志前先检查私人信息。</p></section><footer>教程基于 '
    '{{version}} 一键试用版制作。截图来自此前虚构测试，实际界面主题与窗口尺寸可能不同。</footer>'
    '</main></div></body></html>'
)


def main() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    html = TEMPLATE.replace("{{version}}", project["project"]["version"])
    for name, filename in IMAGES.items():
        image = (ROOT / "artifacts" / filename).read_bytes()
        data = base64.b64encode(image).decode("ascii")
        html = html.replace("{{" + name + "}}", "data:image/png;base64," + data)
    output = ROOT / "dist/RAG-Workbench-试用操作教程.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
