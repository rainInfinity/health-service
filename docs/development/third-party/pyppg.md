# pyPPG 第三方源码基线

## 来源与身份

迁移源目录：旧项目 `nxhe-backend/libs/pyPPG/`，所在仓库远端为 `git@github.com:rainInfinity/nxhe-backend.git`。本地仓库对该目录可见的首个提交为 `4fa0fef108735be50c429376089b29d57376e565`（2026-02-13，提交说明 `first commit`）；没有更早的导入记录或可确认的 pyPPG 版本号。旧目录没有 `LICENSE`、`COPYING` 或源码级 SPDX 声明，旧项目文档仅称其为本地第三方库。

代码结构和 `BmCollection` 等实现与公开候选上游 [godamartonaron/GODA_pyPPG](https://github.com/godamartonaron/GODA_pyPPG) 相符，但本地源码的导入路径和排版已有差异，尚无提交、发布包或逐文件基线证明它来自该仓库的某个确定修订。因此这里只记录候选来源，不把它或 PyPI 的版本号写成本地源码版本。

## 许可状态与发布限制

本地迁移源未附许可证。候选上游仓库 GitHub API 将许可标为 `Other`、SPDX `NOASSERTION`；其 [`LICENSE.txt`](https://github.com/godamartonaron/GODA_pyPPG/blob/main/LICENSE.txt) 写有“academic research purpose only”，并附 GNU GPL v3。现有证据不能证明候选上游就是本地代码的权利人，也不能证明本地副本适用何种授权。

**发布门禁：**在权利人、适用许可证及产品分发权限得到书面确认前，不得将包含该 vendored 源码的服务构建或分发到生产环境。执行本地开发和隔离测试不构成生产授权；本说明不是法律意见。

## 迁入基线

SHA-256 对应旧项目迁入前的 19 个文件；路径相对于 `libs/pyPPG/`。SHA-256 可用于核验迁入源，不表示其上游版本或许可。

| 文件 | SHA-256 |
|---|---|
| `__init__.py` | `0d8c01724c1542c5981ad78b92bc9d41b279e593e57483ec09ae67f78fc30afb` |
| `biomarkers.py` | `b24fe324bd54724c90992d89a8a1b87387ccdfff325bb0bbc9f2e3db2d25b2cd` |
| `datahandling.py` | `e42e40922bc99c7612b10257f240ba441b67b9bc16a966fabb1b3a42b0e3a062` |
| `example.py` | `5fec69094c70eea1c8a662f5b58f870213e00828c406d5a9536b7c8b76e7e639` |
| `fiducials.py` | `0442c4893e94a404c8d2b190bf800a964639817ca91b62c01a815101605fc488` |
| `preproc.py` | `aa9aebd20c25218d27a72dcf55d75f5d9ec6b9eb1301942293eff26c6c417c36` |
| `ppg_sqi.py` | `c17361a62221e30190e4a7ae37bd806df8394f022aae23a5ece281e2d53868a5` |
| `pack_ppg/__init__.py` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `pack_ppg/_ErrorHandler.py` | `d51edd8015cc04d019c19a24bc8decb58fc1fbccc9205e205be65bffcad9877e` |
| `ppg_bm/__init__.py` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `ppg_bm/bm_extraction.py` | `de55f1ed15f380cc46f21278160191586bb86a169fe50540e3c86c58baa7fa56` |
| `ppg_bm/derivs_ratios.py` | `6fefeab366881a325cb234a8e921a7d52d801994d757871379ab9fcbe33a71ab` |
| `ppg_bm/ppg_derivs.py` | `086bdbbf7ecddeb60b2ba092c2e99829fcd836ae56f05ed86c272e04852ccf21` |
| `ppg_bm/ppg_sig.py` | `213d49190fceee2a84c275c72accc538b0bc813aa81a055a7682e215692b8fc7` |
| `ppg_bm/sig_ratios.py` | `7a88328246eeecc5eb2b03e9c12d4f4d6a831a3343d5d6a1030afb9d28af1073` |
| `ppg_bm/statistics.py` | `bb74ae91550cc5c8eb8bef407704055021f22f748be254293ff60802ed7413a0` |
| `validation/__init__.py` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `validation/pw_anal.py` | `3a4e13e19fa2f63b613f510fd3a87e2b52ccf74f45855b3069d14f18499865af` |
| `validation/run_validation.py` | `a980110fc65d0e66628320e9968e5728ea9d2a726e7717641408f3efa8cbef05` |

## 迁入差异

源码位于 `packages/health-inference/src/health_inference/libs/pyPPG/`。迁移保留第三方实现及原有注释；为取消旧顶层 `libs` 别名，以下文件中的绝对导入前缀改为 `health_inference.libs.pyPPG`：

- `__init__.py`、`biomarkers.py`、`datahandling.py`、`example.py`、`fiducials.py`
- `ppg_bm/bm_extraction.py`、`ppg_bm/derivs_ratios.py`、`ppg_bm/ppg_derivs.py`、`ppg_bm/ppg_sig.py`、`ppg_bm/sig_ratios.py`
- `validation/pw_anal.py`、`validation/run_validation.py`

其余迁入文件与上述源文件逐字节相同；另新增 `health_inference/libs/__init__.py` 声明包边界。正式计算只按算法需要导入特征路径，不依赖旧示例、验证脚本或 GUI 数据交互模块。

## 依赖与验证环境

`health-inference` 的 `ppg` 可选项通过 `uv sync --extra ppg` 安装。锁定的直接依赖为 DotMap 1.3.30、NeuroKit2 0.2.12、ONNX Runtime 1.23.2、scikit-learn 1.9.1 和 SciPy 1.16.3；基础依赖 NumPy 2.3.5、pandas 2.3.3 保持在包必需依赖中。旧示例和验证模块使用的 GUI、MNE 与数据集工具未加入此可选项。

本次实际验证环境：Python 3.13.14、NumPy 2.3.5、pandas 2.3.3、SciPy 1.16.3、NeuroKit2 0.2.12、DotMap 1.3.30、scikit-learn 1.9.1、ONNX Runtime 1.23.2。固定合成 PPG 通过新旧 `pyPPG` 路径提取 30 个特征点和 102 个生物标志物统计值；结果逐项相同，最大绝对误差为 0。

在已安装可选依赖的环境中，单独导入并注册基础算法，以及执行 `django.setup()` 和 Django system checks，均未导入 SciPy、NeuroKit2、DotMap、scikit-learn 或 ONNX Runtime。特征和 ONNX 运行路径只在调用时加载这些可选依赖。
