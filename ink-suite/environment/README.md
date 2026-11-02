# 照片入墨：单张国画处理

从国画分身页点击“照片入墨”。进入只取320×180、4FPS原始预览；点击快门或Space之后显示3、2、1，随后POST一张新照片。处理结束保持静态结果，可重新取景或保存/扫码。进入、恢复、错误均不自动拍摄。

使用 `environment/models/figure-ink-v3.onnx`，SHA256为7ca0cfd577415b034bc358f775ad9e2d317de344c07f4482945d2be85562d35b。基于已验证的固定国画人物风格；当前后处理 `figure-mineral-ink-v1` 加强墨色与青绿设色。KV260独立运行ORT 1.19.2，2个CPU线程、关闭自旋，不依赖开发电脑或云端。

输入最长1920×1080，处理最长边640，当前16:9输出640×360；保留原生输出像素保存PNG，不通过放大宣称高清细节。人物会抽象化，必须与实时固定国画角色区分。美感仍以用户验收为准。

界面显示真实阶段和经过秒数，不人为延迟。`PhotoJobs`只允许一个任务；推理在独立子进程，暂停、返回或退出会取消并回收，2秒terminate超时后kill并wait。代次隔离阻止旧任务/旧二维码覆盖新页面。完成作品在同一页面实例暂停恢复后保留。

`EnvironmentPanel(args,on_back=None)`支持嵌入；args包含embed_socket、offline_preview、offline_image、base、works、fps。有on_back时先pause再调用返回函数，无回调时发布menu_requested。stdin支持pause/resume/shutdown。退出前应shutdown并join照片/保存线程；切页用pause/resume。

摄像头仍由共享服务持有。`read_frame`校验RGB几何、新帧身份和预览帧龄；可选关节点不影响拍照成功。进程间临时RGB在任务结束清理；只有用户点保存才将处理后的PNG+JSON写入bodyink-live/works。二维码使用既有作品站，需要同一热点。

模型和ARM运行库由resource_manifest逐文件检查。缺资源先于快门POST报错，不能回退到旧C++滤镜。`ink_filter.py/cpp`为历史实验兼容保留，不是本版照片主流程。

离线验证使用Xvfb、合成HTTP源和既有旧图，不访问新摄像头：在本目录运行 `DISPLAY=:98 python3 -m unittest test_environment test_photo_state test_photo_job test_onnx_photo_worker -v`。真实一次模型输出另见 `../diagnostics/photo-preview-board-20260926/`；取消/超时/回收测试使用快速可控工作进程，避免反复运行完整风格模型。
