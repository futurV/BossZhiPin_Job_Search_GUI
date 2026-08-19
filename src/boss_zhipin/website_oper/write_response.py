"""单个岗位的主循环：抓 JD → 生成招呼语 → 校验 → 发送（或 dry-run）。

调用图：

.. code-block:: text

    main.py
      └─ send_job_descriptions_to_chat
           ├─ finding_jobs.open_browser_with_options / log_in
           ├─ finding_jobs.select_dropdown_option (一次)
           └─ while True:
                ├─ finding_jobs.get_job_description_by_index
                ├─ generate_letter (RAG + OpenAI 兼容端点)
                ├─ validate_letter / log_attempt
                └─ finding_jobs.{click_by_xpath,wait_for_css,send_chat_message,navigate_back}

终止条件：
- 连续 ``MAX_CONSECUTIVE_MISSES`` 次拿不到 JD（推测到列表底部）
- 任何 exception 命中外层 try
"""

from __future__ import annotations

import asyncio
import logging
import os
import random

from boss_zhipin.audit import log_attempt, log_sent_application, validate_letter
from boss_zhipin.gui.events import emit as _emit_progress
from boss_zhipin.models.job_matcher import should_apply
from boss_zhipin.models.llm import current_provider_label, generate_letter
from boss_zhipin.website_oper import finding_jobs

log = logging.getLogger(__name__)

# 设定后，岗位仍会走既有的关键词、向量和 LLM 匹配筛选；只有通过筛选的岗位才
# 使用这段固定招呼语。留空则保持原来的按 JD 生成方式。
FIXED_GREETING_ENV = "BOSS_FIXED_GREETING"


class ReturnToListError(RuntimeError):
    """招呼语已经发出、但没能退回岗位列表。

    单独一个异常类型，让主循环能区分「消息压根没发出去」和「消息已送达、只是没回到
    列表」——后者必须照常记 ``sent=True``，否则会漏记真实已发送的招呼语，重跑时可能
    重复联系同一个招聘者。仍是 ``RuntimeError`` 子类，对外行为不变。
    """


async def send_response_and_go_back(response: str) -> str:
    """点击“立即沟通”，确认 BOSS 自动发送，并关闭结果层。

    ``response`` 只作为弹窗无法提取实际文案时的审计兜底；真正发送的是 BOSS
    账号预设招呼语。返回实际确认文案，供 ``letters.jsonl`` 准确记账。
    """
    result = await finding_jobs.click_contact_and_confirm(timeout=20)
    if not result.get("ok"):
        raise RuntimeError(f"点击立即沟通后未确认发送: {result.get('reason', 'unknown')}")
    actual_greeting = (result.get("greeting") or response).strip()
    if not await finding_jobs.dismiss_contact_confirmation():
        raise ReturnToListError("招呼语已送达，但未能关闭发送结果层")
    return actual_greeting


def _int_env(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning("%s=%r 不是整数，回退默认 %d", name, raw, default)
        return default
    if minimum is not None:
        return max(minimum, value)
    return value


def _float_env(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        log.warning("%s=%r 不是数字，回退默认 %.1f", name, raw, default)
        return default
    if minimum is not None:
        return max(minimum, value)
    return value


def _send_delay_range() -> tuple[float, float]:
    min_delay = _float_env("BOSS_AUTO_SEND_DELAY_MIN", 10.0, minimum=0.0)
    max_delay = _float_env("BOSS_AUTO_SEND_DELAY_MAX", 60.0, minimum=0.0)
    if max_delay < min_delay:
        log.warning(
            "BOSS_AUTO_SEND_DELAY_MAX=%.1f 小于 MIN=%.1f，已交换两者",
            max_delay,
            min_delay,
        )
        return max_delay, min_delay
    return min_delay, max_delay


async def send_job_descriptions_to_chat(
    usr_name: str,
    url: str,
    browser_type: str,
    label: str,
    vectorstore=None,
    dry_run: bool = False,
    resume_keywords: list[str] | None = None,
    resume_text: str | None = None,
    min_keyword_match: int = 2,
    min_llm_score: int = 70,
    exclude_keywords: list[str] | None = None,
    max_successful_sends: int | None = None,
) -> None:
    """主循环（async）。

    用哪个 LLM 端点 / model 由 ``LLM_*`` 环境变量决定（见 ``llm._build_client``），
    不再按 provider 分支。``label`` 为空字符串时跳过下拉筛选，沿用 BOSS 默认推荐 feed。

    ``dry_run=True`` 时不点"立即沟通"，但 LLM 仍会调、招呼语仍会校验和写日志，
    用来调 prompt。

    整段必须包在 ``uc.loop().run_until_complete(...)`` 里一次性跑完，
    不能拆成多个 ``run_until_complete`` —— nodriver CDP 在事件循环停顿期间
    会进入半死状态，下次 evaluate 直接 hang。详见模块 docstring。
    """
    # audit log（letters.jsonl）那一列的 provider/model 标签——从当前 LLM_* 推。
    # 真实运行时 token / 成本由 generate_letter 里的 telemetry 单独记。
    llm_model = os.getenv("LLM_MODEL", "").strip()
    provider_label = current_provider_label()

    await finding_jobs.open_browser_with_options(url, browser_type)
    _emit_progress("browser_started")
    await finding_jobs.log_in()
    _emit_progress("login_ok")

    job_index = 1
    visible_index = 1
    iteration = 0
    consecutive_misses = 0
    sent_count = 0
    # GUI 直接传数值，避免默认值 / 环境变量加载时序影响本轮上限；CLI 未传时仍从
    # BOSS_AUTO_SEND_MAX_SENT 读取，保持既有用法。
    max_sent = (
        max(1, max_successful_sends)
        if max_successful_sends is not None
        else _int_env("BOSS_AUTO_SEND_MAX_SENT", 50, minimum=1)
    )
    send_delay_min, send_delay_max = _send_delay_range()
    _emit_progress(
        "run_config",
        max_successful_sends=max_sent,
        delay_min_seconds=send_delay_min,
        delay_max_seconds=send_delay_max,
        dry_run=dry_run,
    )
    # 一旦 LLM 评分走了 fail-open（缺配置 / 调用挂 / 解析不出分），第二层过滤其实在
    # 全放行。只在本轮**首次**遇到时提示一次，别每个岗位刷屏。
    degraded_warned = False
    # 推荐 feed 末尾、或者某条岗位卡 DOM 没渲染好，都会让 get_job_description
    # 返回 None。连续 N 次拿不到就当列表到底了停掉，否则 job_index 会无限涨。
    MAX_CONSECUTIVE_MISSES = 5
    selection_ok = await finding_jobs.select_dropdown_option(label)
    if selection_ok is False:
        message = f"求职期望 {label!r} 未能在 BOSS 页面成功激活，本轮已停止，避免误用默认推荐流。"
        log.error(message)
        _emit_progress("error", stage="expectation_not_activated", message=message)
        return
    # 岗位卡必须真的渲染出内容才能开跑。只判 .job-card-box 存在不够：SPA 没
    # boot 起来时 DOM 里可能已有骨架屏空卡，选择器命中但正文全空。
    if not await finding_jobs.wait_for_real_job_cards(timeout=30):
        log.warning("等待 30s 仍无真实岗位卡，刷新页面重试")
        await finding_jobs.reload_page()
        # reload 冲掉了 select_dropdown_option 点出来的客户端筛选状态（它只点
        # chip / 下拉，不改 URL）。不重选就会拿 BOSS 默认推荐 feed 当成用户选的
        # tag 一路发下去，所以这里必须重来一次。
        selection_ok = await finding_jobs.select_dropdown_option(label)
        if selection_ok is False:
            message = f"页面刷新后求职期望 {label!r} 未能重新激活，本轮已停止。"
            log.error(message)
            _emit_progress("error", stage="expectation_not_activated", message=message)
            return
        if not await finding_jobs.wait_for_real_job_cards(timeout=30):
            # 实测过的根因：profile 的 HTTP 缓存里存了 SPA vendor 脚本的 5xx 错误
            # 响应（CDN 抖动时缓存下来的），Chrome 一直重放这份坏缓存，Vue app 永远
            # boot 不起来，页面死在「加载中，请稍候」。清缓存即可，登录态不受影响。
            message = (
                "岗位列表始终没渲染出来，页面可能卡在「加载中，请稍候」。"
                "常见原因是 Chrome profile 缓存里存了 SPA 脚本的错误响应。"
                f"解法：关掉脚本，删掉 {finding_jobs.CHROME_PROFILE_DIR} 里 "
                "Default/Cache 和 Default/Code Cache 两个目录"
                "（别删 Default/Network，登录 cookie 在里面），然后重跑。"
            )
            log.error(message)
            # 必须在这里收尾。继续进主循环的话，骨架屏空卡是点得动的，每轮都会
            # 白等 10s JD 超时，5 轮后照样上报 feed_exhausted —— 那正是本次要修掉
            # 的误报：明明是页面没加载出来，却告诉用户「已经到底了」。
            _emit_progress("error", stage="job_list_never_rendered", message=message)
            return
    while True:
        try:
            iteration += 1
            log.info(
                "=== 第 %d 轮: 处理 job_index=%d visible_index=%d ===",
                iteration,
                job_index,
                visible_index,
            )
            job_description = await finding_jobs.get_job_description_by_index(
                visible_index
            )
            if job_description:
                consecutive_misses = 0
                job_metadata = await finding_jobs.get_job_metadata_by_index(
                    visible_index
                )
                _emit_progress(
                    "job_found",
                    index=job_index,
                    jd_preview=job_description[:80],
                    application_status="待判断",
                )
                contact_state = await finding_jobs.get_contact_button_state()
                element = contact_state.get("text", "")
                log.info(
                    "chat 按钮: text=%r disabled=%s",
                    element,
                    contact_state.get("disabled"),
                )
                if element == "立即沟通" and contact_state.get("disabled"):
                    log.info("⏭️ [跳过 #%d] 已沟通过该岗位", job_index)
                    _emit_progress(
                        "job_skipped",
                        index=job_index,
                        reason="already_contacted",
                        detail="立即沟通按钮已失效，判定已沟通过",
                        application_status="未投递",
                        application_reason="该岗位已沟通过",
                    )
                    job_index += 1
                    visible_index += 1
                    await asyncio.sleep(1)
                    continue
                if element == "立即沟通":
                    # ====== 多层过滤：黑名单 + 关键词 + 向量 + LLM ======
                    if resume_text:
                        apply, details = await asyncio.to_thread(
                            should_apply,
                            job_description,
                            resume_keywords,
                            resume_text,
                            min_keyword_match,
                            min_llm_score,
                            exclude_keywords,
                            vectorstore,
                        )
                        # 评分降级（fail-open）首次出现时显式告警一次——否则用户只看到
                        # 一堆岗位"通过"，不知道第二层 LLM 过滤其实没在跑。
                        if details.get("scoring_degraded") and not degraded_warned:
                            degraded_warned = True
                            log.warning(
                                "⚠️ LLM 评分暂时不可用（%s），本轮所有岗位按放行处理",
                                details.get("reason", ""),
                            )
                            _emit_progress(
                                "scoring_degraded", detail=details.get("reason", "")
                            )
                        if not apply:
                            stage = details.get("stage", "unknown")
                            if stage == "blacklist":
                                log.info(
                                    "⏭️ [跳过 #%d] 触发黑名单: %s",
                                    job_index,
                                    details["reason"],
                                )
                            elif stage == "keyword":
                                log.info(
                                    "⏭️ [跳过 #%d] 关键词匹配不足: 命中 %s - %s",
                                    job_index,
                                    details["matched_keywords"],
                                    details["reason"],
                                )
                            elif stage == "vector_search":
                                log.info(
                                    "⏭️ [跳过 #%d] 语义不匹配: %s",
                                    job_index,
                                    details["reason"],
                                )
                            else:
                                log.info(
                                    "⏭️ [跳过 #%d] LLM 评分 %s/%s: %s",
                                    job_index,
                                    details.get("score"),
                                    details.get("threshold"),
                                    details.get("reason"),
                                )
                            _emit_progress(
                                "job_skipped",
                                index=job_index,
                                reason=stage,
                                detail=details.get("reason", ""),
                                score=details.get("score"),
                                threshold=details.get("threshold"),
                                matched=details.get("matched_keywords"),
                                application_status="未投递",
                                application_reason=details.get("reason", "岗位不匹配"),
                            )
                            job_index += 1
                            visible_index += 1
                            await asyncio.sleep(3)
                            continue
                        log.info(
                            "✅ [匹配 #%d] 关键词命中: %s",
                            job_index,
                            details["matched_keywords"],
                        )
                        if "score" in details:
                            log.info(
                                "   LLM 评分: %s/100 - %s",
                                details["score"],
                                details["reason"],
                            )
                    # ====== 过滤结束 ======

                    # LLM 评分（resume_text 为空时没跑过滤 → None），带进 letter_sent 事件，
                    # 让 GUI 进度面板能看到"这条招呼语对应的岗位匹配多少分"。
                    match_score = details.get("score") if resume_text else None

                    fixed_greeting = os.getenv(FIXED_GREETING_ENV, "").strip()
                    if fixed_greeting:
                        response = fixed_greeting
                        log.info("使用 BOSS_FIXED_GREETING 固定招呼语")
                    else:
                        # LLM 调用是同步阻塞的 HTTP 请求，扔到 thread pool 跑
                        # 避免阻塞事件循环 → 卡死 nodriver CDP heartbeat
                        response = await asyncio.to_thread(
                            generate_letter,
                            usr_name,
                            vectorstore,
                            job_description,
                        )

                    validation = validate_letter(response)

                    if not validation.ok:
                        log.warning(
                            "[BLOCKED] %s — preview: %r",
                            validation.reasons,
                            response[:80],
                        )
                        log_attempt(
                            provider=provider_label,
                            model=llm_model,
                            job_description=job_description,
                            letter=response,
                            validation=validation,
                            dry_run=dry_run,
                            sent=False,
                        )
                        _emit_progress(
                            "letter_sent",
                            index=job_index,
                            status="blocked",
                            score=match_score,
                            letter_len=len(response),
                            application_status="未投递",
                            application_reason="招呼语校验未通过",
                        )
                    elif dry_run:
                        log.info(
                            "[DRY-RUN] 招呼语 (%d 字符) 不发送。--- letter ---\n%s\n--------------",
                            len(response),
                            response,
                        )
                        log_attempt(
                            provider=provider_label,
                            model=llm_model,
                            job_description=job_description,
                            letter=response,
                            validation=validation,
                            dry_run=True,
                            sent=False,
                        )
                        _emit_progress(
                            "letter_sent",
                            index=job_index,
                            status="dry_run",
                            score=match_score,
                            letter_len=len(response),
                            application_status="未投递",
                            application_reason="模拟运行（Dry Run）不发送",
                        )
                    else:
                        log.info("发送招呼语：%s", response)
                        await asyncio.sleep(1)
                        # 新版 BOSS 点击“立即沟通”就自动发送账号预设招呼语，并在当前
                        # 岗位页显示结果层；不存在首轮可编辑的 #chat-input。
                        try:
                            response = await send_response_and_go_back(response)
                            returned_to_list = True
                        except ReturnToListError as e:
                            log.warning("%s；招呼语已发送，照常记录后结束本轮", e)
                            returned_to_list = False
                        sent_count += 1
                        log_attempt(
                            provider=provider_label,
                            model=llm_model,
                            job_description=job_description,
                            letter=response,
                            validation=validation,
                            dry_run=False,
                            sent=True,
                        )
                        try:
                            csv_added = log_sent_application(
                                job_metadata=job_metadata,
                                job_description=job_description,
                                letter=response,
                                provider=provider_label,
                                model=llm_model,
                                label=label,
                                match_score=match_score,
                                match_reason=(
                                    details.get("reason", "") if resume_text else ""
                                ),
                                matched_keywords=(
                                    details.get("matched_keywords", [])
                                    if resume_text
                                    else []
                                ),
                            )
                        except Exception as csv_error:  # noqa: BLE001
                            # 消息已经真实送达，CSV 审计失败不能把连续投递主循环打断。
                            # 最常见原因是用户正用 Excel 打开文件导致 Windows 文件锁。
                            log.exception(
                                "投递已发送，但 CSV 写入失败（%s / %s）: %s",
                                job_metadata.get("company_name", "未知公司"),
                                job_metadata.get("job_title", "未知职位"),
                                csv_error,
                            )
                        else:
                            log.info(
                                "投递 CSV：%s（%s / %s）",
                                "已新增" if csv_added else "重复岗位，未重复写入",
                                job_metadata.get("company_name", "未知公司"),
                                job_metadata.get("job_title", "未知职位"),
                            )
                        _emit_progress(
                            "letter_sent",
                            index=job_index,
                            status="sent",
                            score=match_score,
                            letter_len=len(response),
                            application_status="已投递",
                            application_reason="已点击立即沟通并确认发送",
                        )
                        if not returned_to_list:
                            # 已经卡在沟通页、回不到列表，继续循环只会一直拿不到 JD；
                            # 招呼语已记录，干净收尾而不是让异常冒泡把这条发送吞掉。
                            _emit_progress(
                                "feed_exhausted",
                                total=job_index,
                                sent_count=sent_count,
                                max_successful_sends=max_sent,
                                reason="已发送，但未能回到岗位列表",
                            )
                            break
                        if sent_count >= max_sent:
                            log.info(
                                "已发送 %d 条，达到 BOSS_AUTO_SEND_MAX_SENT 上限，结束",
                                sent_count,
                            )
                            _emit_progress(
                                "feed_exhausted",
                                total=job_index,
                                sent_count=sent_count,
                                max_successful_sends=max_sent,
                                reason="达到成功投递上限",
                            )
                            break
                        delay = random.uniform(send_delay_min, send_delay_max)
                        log.info("发送后等待 %.1f 秒再继续", delay)
                        await asyncio.sleep(delay)
                else:
                    # 非「立即沟通」通常代表职位已关闭、对方状态异常或页面结构变动。
                    # 不能静默略过，否则 GUI 看起来像处理过却不知道为何没有投递。
                    reason = element or "未找到立即沟通按钮"
                    log.info("⏭️ [跳过 #%d] 无法发起沟通: %s", job_index, reason)
                    _emit_progress(
                        "job_skipped",
                        index=job_index,
                        reason="contact_unavailable",
                        detail=f"沟通按钮状态：{reason}",
                        application_status="未投递",
                        application_reason=f"沟通按钮不可用：{reason}",
                    )
            else:
                consecutive_misses += 1
                log.info(
                    "job_index=%d 拿不到 JD（连续第 %d 次）",
                    job_index,
                    consecutive_misses,
                )
                # 到底判断放在滚动之前：哪怕下面滚动每次都"成功"，只要累计够多次拿不到
                # JD 就收尾。否则在近乎无限的推荐 feed 上，滚动永远返回 True → 每轮都
                # continue，这个 break 永远够不着，循环空转到发送上限或异常才停。
                if consecutive_misses >= MAX_CONSECUTIVE_MISSES:
                    log.info(
                        "连续 %d 个岗位拿不到，推测已到推荐 feed 列表底部，结束",
                        MAX_CONSECUTIVE_MISSES,
                    )
                    _emit_progress(
                        "feed_exhausted",
                        total=job_index - 1,
                        sent_count=sent_count,
                        max_successful_sends=max_sent,
                        reason="岗位列表已到底",
                    )
                    break
                loaded_count = await finding_jobs.get_loaded_job_count()
                if loaded_count and visible_index > loaded_count:
                    log.info(
                        "当前只加载了 %d 个岗位，尝试滚动加载第 %d 个岗位",
                        loaded_count,
                        visible_index,
                    )
                if await finding_jobs.scroll_to_load_more_jobs():
                    # 不在这里把 consecutive_misses 归零：滚动成功只说明页面还能动，不代表
                    # 这个 index 拿得到 JD。只有真正抓到 JD（循环顶部那次归零）才算有进展，
                    # 这样上面的到底判断才不会被"滚得动"无限推迟。
                    latest_loaded_count = await finding_jobs.get_loaded_job_count()
                    if (
                        latest_loaded_count >= 5
                        and visible_index > latest_loaded_count
                    ):
                        log.info(
                            "岗位列表是虚拟滚动，重置可见索引：%d -> %d",
                            visible_index,
                            latest_loaded_count,
                        )
                        visible_index = latest_loaded_count
                    await asyncio.sleep(1)
                    continue

            await asyncio.sleep(3)
            job_index += 1
            visible_index += 1

        except Exception as e:
            log.exception("主循环抛异常: %s", e)
            _emit_progress(
                "error",
                stage=f"job_index={job_index}",
                message=f"{type(e).__name__}: {e}",
                application_status="未投递",
                application_reason="本岗位处理异常，未确认发送",
            )
            break
