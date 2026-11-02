# 局域网账户与记录归属

本版网站改为注册账户登录。用户从当前主菜单二维码进入后，先登录或注册，再显式
关联本次游玩。关联凭据只适用于当前尚未归属的活动会话；已归属他人的会话不能
重新认领。旧匿名历史仍保留在磁盘和原数据库中，但不会自动分给新注册账户。

新增表为 `accounts`、`account_logins`、`account_sessions`、`account_legacy_sessions`；旧 `sessions`、`rounds`、
`artworks`、`photo_jobs` 的字段和已有记录不做迁移改写。账户密码以独立随机盐和
PBKDF2-SHA256 摘要保存；浏览器登录令牌只保存其 SHA256，支持主动退出与过期。
局域网HTTP使用 HttpOnly、SameSite=Lax、Path=/ cookie，不增加第三方依赖。

## HTTP 契约

所有写入请求要求 `X-Ink-Visitor: 1`、同源 Origin（如存在）和 JSON Content-Type。
账户请求最大2048字节。注册/登录每个来源IP在5分钟内最多30次尝试。

- `GET /api/account`：`{authenticated:false}` 或
  `{authenticated:true,account:{id,username,name},sessions:[...],active_token}`。
  sessions仅包含本人最近50次记录，含 `token/name/status/started/ended/current`。
- `POST /api/account/register`：`{username,password,name?}`。用户名3–32字符，
  支持文字、数字、字母、点、短横线、下划线；密码8–128字符；昵称1–24字符。
- `POST /api/account/login`：`{username,password}`。注册/登录成功设置cookie，返回账户状态。
- `POST /api/account/logout`：`{}`。撤销此次浏览器登录并删除cookie。
- `POST /api/account/claim`：`{token}`。显式关联当前扫码会话；已关联本人时幂等。
- `GET /api/account/sessions?offset=0&limit=50`：本人分页记录，返回
  `{sessions,offset,limit,has_more}`，limit为1–50。
- `POST /api/account/ai/clear`：`{}`。删除本账户所有已关联游玩的AI建议（含归档），
  取消待处理记录，迟到响应不能重建记录。作品、成绩、照片保留。
- `GET /api/projects`：公开本地静态项目讲解，不含任何用户历史。

旧 `/api/visitor/<token>` API、全部原图/照片/报告下载路径以及AI接口继续存在，
但必须同时具备cookie登录与该会话归属。无登录401，尚未关联409，属于他人403，
无效记录404。二维码HTML落地页仅为应用外壳，不包含私有记录。

排行榜只向已登录用户返回注册账户的公开昵称、游戏、规则、名次与成绩；不返回
其他人的会话token、原图、照片、AI建议。板端直接对象调用维持原来的可信本机
功能，HTTP没有通过localhost、转发头或自称internal绕过账户验证的入口。

原有单会话AI清空API保留活动会话语义。原生界面可调用
`AIService.clear_owner(current_token)`：绑定账户时清该账户全部AI历史，未绑定时
只清当前匿名游玩，不涉及其他人。

## 升级时的一次性匿名记录封存

部署期间先暂停应用，再显式运行（不会在普通启动中自动运行）：

```bash
python3 -B /path/to/visitor/account_migrate.py --database /home/ubuntu/ink-suite/data/visitors/visitors.sqlite3 --seal-id accounts-20261024
```

脚本把此刻所有尚未绑定账户的会话（包括当前活动会话）记入独立封存表，并在
`account_migrations` 记下此次迁移。旧核心表、作品、照片、成绩和AI记录均不改动，
也不结束游玩或清空画卷。访问封存二维码会收到409、code为`legacy_session`：
“这是升级前的匿名记录。请在板端结束本次游玩，再重新扫码绑定新记录。”

相同seal-id重复运行只读回原迁移，不会封存升级后新建的会话。正常新会话可按
原流程扫码关联。部署回退时，受控执行同脚本加`--undo`只撤除此seal-id的封存
标记与迁移记录，不删除或重写用户数据；网站没有撤销接口。
