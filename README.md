# RainClassroom-Video-Watcher-GUI

[RainClassroom-Video-Watcher](https://github.com/Accurio/RainClassroom-Video-Watcher)的图形界面版本。基于 Flet 构建。


>如果你付费获得了本软件，那么你被骗了

## 功能

* 自动观看雨课堂指定课程的全部视频。
* 图形界面填写 `authority`、`classroom_id`、`session_id`、`csrftoken`、`xtbz`，无需手动编辑脚本。
* 内置基于 Selenium + Edge 的浏览器控制器，进入课程页后一键抓取 Cookie 并回填。
* 保留原始 CLI 脚本 [`RainClassroomVideoWatcher.py`](RainClassroomVideoWatcher.py)，无 GUI 场景下仍可使用。

## 环境要求

* Windows 10/11
* Python >= 3.10
* Microsoft Edge 浏览器（仅在使用「浏览器」取 Cookie 功能时需要）

## 安装

```powershell
git clone https://github.com/Requiem1ovo/RainClassroom-Video-Watcher-GUI.git
cd RainClassroom-Video-Watcher-GUI
pip install -r requirements.txt
```
## 使用方法（GUI）

1. 在项目目录下执行：

   ```powershell
   python main.py
   ```

2. 在主界面填写或调整以下字段：

   | 字段 | 含义 | 示例 |
   | :-- | :-- | :-- |
   | `authority` | 雨课堂域名 | `changjiang.yuketang.cn` |
   | `classroom_id` | 课程编号 | `12345678` |
   | `session_id` | Cookie 中的 `sessionid` | 浏览器 DevTools 取 |
   | `csrftoken` | Cookie 中的 `csrftoken` | 浏览器 DevTools 取 |
   | `xtbz` | 平台标识 | `ykt` |

3. 点击「浏览器」按钮，程序会启动 Edge 并自动打开雨课堂：

   * 在自动打开的 Edge 中登录并进入需要刷课的课程页面；
   * 点击「获取 Cookie」按钮，程序会把 `classroom_id` / `sessionid` / `csrftoken` 自动回填到主界面表单；
   * 如果不想使用浏览器获取 Cookie，也可以按下面的[手动操作](#手动操作)一栏自行从 DevTools 复制粘贴。

4. 回到主界面，点击「运行」按钮


## 手动操作

> 以下为CLI 脚本 [`RainClassroomVideoWatcher.py`](RainClassroomVideoWatcher.py) 的使用说明，仅在不方便使用 GUI 时参考。

### 功能

自动观看雨课堂指定课程的全部视频。

### 使用方法

1. 确保已安装 `Python>=3.10` 和 `HTTPX`，`HTTPX` 可通过 `pip install httpx[http2]` 安装。
1. 下载 [`RainClassroomVideoWatcher.py`](RainClassroomVideoWatcher.py) 至本地；
2. 浏览器访问雨课堂网站并进入需要自动观看视频的课程，  
   网址如 `https://changjiang.yuketang.cn/v2/web/studentLog/12345678`，  
   `authority` 为网址域名即 `changjiang.yuketang.cn`，  
   `classroom_id` 为网址最后的数字如 `12345678`；
3. 打开开发者工具（<kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>I</kbd>），点击上侧活动栏的『应用程序』，点击展开左侧『Cookies』并点击雨课堂网址，查看 `sessionid`、`csrftoken`、`xtbz`（见下图）；
4. 打开下载至本地的 `RainClassroomVideoWatcher.py`，将使用上述数据填写相关变量；
5. 在终端运行 `RainClassroomVideoWatcher.py`。

![开发者工具](DevTools.png)

## 常见问题

* **Edge 启动失败 / 找不到驱动**  
  请确认本机已安装 Microsoft Edge；Selenium 4 会自动管理 Edge WebDriver，无需手动下载。
* **`classroom_id` 必须为数字**  
  课程页 URL 末尾的纯数字编号，不要带其他字符。
* **刷课过程中报 `RuntimeError`**  
  通常是 Cookie 过期或 `classroom_id` 填错，重新获取 Cookie 再运行即可。
* **卡在`Preparing Flet v0.85.2 for the first use. This is a one-time operation...`不动**  
   Flet 正在后台进行下载，可以使用VPN加速下载

## 许可

基于 [AGPLv3](LICENSE) 开源。