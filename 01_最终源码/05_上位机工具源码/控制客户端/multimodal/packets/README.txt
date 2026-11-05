先发送 display_default.hex，然后发送 mode_digit.hex 或 mode_hanzi.hex。
等待每个控制命令的 5A43 回复，再依次发送目标目录中的 01.hex 到 05.hex，均使用 HEX 模式。
这五块拼成一条原 A559 图像请求，不要加 CR/LF，不要重复同步头，块间隔必须小于60秒。
等待完整 5A59 回复后再发送下一张图。digit8 应显示8，up/down/left/right 应显示对应箭头。
这些是通过RTL仿真的样例输入，不是远程板验证记录；需先用PDS编译并烧录新工程。
