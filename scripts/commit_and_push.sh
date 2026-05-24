#!/bin/bash
# scripts/commit_and_push.sh — 自动化提交并推送到 fork 仓库
# 用法: ./scripts/commit_and_push.sh "commit message"
#
# 只推送到 fork (xiaodutou/TradingAgents-CN-xdt)，不创建上游 PR
# 自动处理: git身份、gh认证、远程冲突rebase、分支推送

set -euo pipefail

REPO_OWNER="xiaodutou"
REPO_NAME="TradingAgents-CN-xdt"
FORK_OWNER="xiaodutou"
FORK_REPO="TradingAgents-CN-xdt"
GIT_NAME="xiaodutou"
GIT_EMAIL="xiaodutou@users.noreply.github.com"

# ========== 颜色输出 ==========
RED='\033[0;31m' GREEN='\033[0;32m' YELLOW='\033[1;33m' NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ========== 前置检查 ==========
check_prerequisites() {
    if ! command -v gh &>/dev/null; then
        error "gh CLI 未安装，请先安装: https://cli.github.com/"
        exit 1
    fi

    if ! gh auth status &>/dev/null 2>&1; then
        error "GitHub 未认证，请运行: gh auth login"
        exit 1
    fi

    # 确保 git 身份已配置
    if [ -z "$(git config user.name)" ] || [ -z "$(git config user.email)" ]; then
        warn "未配置 git 身份，自动设置为 ${GIT_NAME} <${GIT_EMAIL}>"
        git config user.name "${GIT_NAME}"
        git config user.email "${GIT_EMAIL}"
    fi
}

# ========== 参数解析 ==========
COMMIT_MSG="${1:-}"
if [ -z "${COMMIT_MSG}" ]; then
    echo "用法: $0 \"commit message\""
    echo ""
    echo "示例:"
    echo "  $0 \"fix: 修复 PB 计算错误\""
    echo "  $0 \"feat: 添加新功能\""
    exit 1
fi

# ========== 检查是否有变更 ==========
STAGED_FILES=$(git status --porcelain | grep -c '^[ MARCD]' 2>/dev/null || echo 0)
UNSTAGED_FILES=$(git diff --name-only 2>/dev/null | wc -l)

if [ "${STAGED_FILES}" -eq 0 ] && [ "${UNSTAGED_FILES}" -eq 0 ]; then
    warn "没有需要提交的文件变更"
    exit 0
fi

# ========== 提交变更 ==========
info "提交 ${STAGED_FILES} + ${UNSTAGED_FILES} 个文件..."

# 确保所有修改的文件都被暂存
git add -A

# 执行提交
git -c user.name="${GIT_NAME}" -c user.email="${GIT_EMAIL}" commit -m "${COMMIT_MSG}"

info "提交完成: $(git log --oneline -1)"

# ========== 推送到 fork 仓库 ==========
info "推送到 fork 仓库 (xiaodutou/TradingAgents-CN-xdt)..."

# 确保 xdt remote 存在
if ! git remote | grep -q "^xdt$"; then
    git remote add xdt "https://github.com/${FORK_OWNER}/${FORK_REPO}.git"
fi

CURRENT_BRANCH=$(git branch --show-current)

# 尝试拉取 fork 上的同分支变更（避免冲突）
if [ "${CURRENT_BRANCH}" = "main" ]; then
    git -c credential.helper="!gh auth git-credential" pull xdt main --rebase 2>/dev/null || {
        warn "Rebase 有冲突，中止推送"
        git rebase --abort 2>/dev/null || true
        error "请先手动处理冲突"
        exit 1
    }
else
    # feature 分支：尝试拉取同分支，如果不存在则跳过
    git -c credential.helper="!gh auth git-credential" pull xdt "${CURRENT_BRANCH}" --rebase 2>/dev/null || {
        warn "远程分支 ${CURRENT_BRANCH} 不存在或 rebase 冲突，跳过 pull"
        git rebase --abort 2>/dev/null || true
    }
fi

# 推送到 fork
if [ "${CURRENT_BRANCH}" = "main" ]; then
    git -c credential.helper="!gh auth git-credential" push xdt main
else
    git -c credential.helper="!gh auth git-credential" push xdt "${CURRENT_BRANCH}" 2>/dev/null || {
        git -c credential.helper="!gh auth git-credential" push --set-upstream xdt "${CURRENT_BRANCH}"
    }
fi

info "推送完成"
info "查看 fork: https://github.com/xiaodutou/TradingAgents-CN-xdt"
if [ "${CURRENT_BRANCH}" != "main" ]; then
    info "创建 PR: https://github.com/xiaodutou/TradingAgents-CN-xdt/compare/main...${CURRENT_BRANCH}"
fi
