/* Local interaction adapter. No remote code, prompt HTML, or model-generated JavaScript.
 * Pinning behavior follows open-claude-ui (MIT, Caho1) and assistant-ui's documented
 * scroll-anchor interaction. Independently implemented for Streamlit's DOM lifecycle.
 */
(() => {
  // Streamlit renders popovers outside stApp. Mirror the app's selected theme,
  // rather than assuming the operating-system theme also applies to that portal.
  window.__ragWorkbenchThemeObserver?.disconnect();
  const app = document.querySelector('[data-testid="stApp"]');
  if (app) {
    const syncTheme = () => {
      document.documentElement.dataset.wbTheme = getComputedStyle(app).colorScheme.includes("dark") ? "dark" : "light";
    };
    syncTheme();
    const themeObserver = new MutationObserver(syncTheme);
    themeObserver.observe(app, { attributes: true, attributeFilter: ["class", "style"] });
    window.__ragWorkbenchThemeObserver = themeObserver;
  }
  window.__ragWorkbenchInteraction?.dispose();
  window.__ragWorkbenchMenus?.abort();
  const menuController = new AbortController();
  window.__ragWorkbenchMenus = menuController;
  const dismissMenu = () => {
    document.querySelector('[data-testid="stPopoverButton"][aria-expanded="true"]')?.click();
  };
  window.addEventListener("resize", dismissMenu, { signal: menuController.signal });
  const sidebar = document.querySelector('[data-testid="stSidebar"]');
  if (sidebar) {
    const sidebarObserver = new MutationObserver(() => {
      const opener = sidebar.querySelector('[data-testid="stPopoverButton"][aria-expanded="true"]');
      if (opener && (sidebar.dataset.collapsed === "true" || sidebar.getBoundingClientRect().right <= 0)) dismissMenu();
    });
    sidebarObserver.observe(sidebar, { attributes: true, attributeFilter: ["data-collapsed", "style", "class"] });
    menuController.signal.addEventListener("abort", () => sidebarObserver.disconnect(), { once: true });
  }
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape" || event.isComposing) return;
    const opener = document.querySelector('[data-testid="stPopoverButton"][aria-expanded="true"]');
    if (!opener) return;
    opener.click();
    opener.focus();
    event.preventDefault();
    event.stopImmediatePropagation();
  }, { capture: true, signal: menuController.signal });
  // Wait for click completion and React's own dismissal. Never toggle another opener.
  document.addEventListener("click", event => {
    const opener = document.querySelector('[data-testid="stPopoverButton"][aria-expanded="true"]');
    const menu = document.querySelector('[data-testid="stPopoverBody"]');
    if (!opener || !menu || !(event.target instanceof Element)) return;
    if (menu.contains(event.target) || event.target.closest('[data-testid="stPopoverButton"]')) return;
    setTimeout(() => {
      if (!menuController.signal.aborted && opener.isConnected && opener.getAttribute("aria-expanded") === "true") opener.click();
    }, 0);
  }, { signal: menuController.signal });
  const scope = [...document.querySelectorAll(".interaction-scope")].at(-1);
  const main = document.querySelector('[data-testid="stMain"]');
  const content = document.querySelector('[data-testid="stMainBlockContainer"]');
  if (scope?.dataset.notice === "true") {
    requestAnimationFrame(() => document.querySelector(".st-key-settings_feedback")?.scrollIntoView({ block: "nearest", behavior: "instant" }));
  }
  if (!scope || !main || !content || scope.dataset.page !== "AI 聊天") return;

  const controller = new AbortController();
  const signal = controller.signal;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const views = window.__ragWorkbenchViews ||= new Map();
  const session = scope.dataset.session;
  const existing = views.has(session);
  const view = views.get(session) || { follow: true, count: 0, top: 0 };
  views.set(session, view);
  if (views.size > 20) views.delete(views.keys().next().value);

  let frame = 0;
  let programmatic = false;
  let lastInput = -Infinity;
  let disposed = false;
  let touchY = null;
  let detailsResize = null;
  const timers = new Set();
  const jump = document.createElement("button");
  const jumpDock = document.createElement("div");
  jumpDock.className = "wb-jump-dock";
  jumpDock.hidden = true;
  jump.type = "button";
  jump.className = "wb-jump-latest";
  jump.textContent = "↓";
  jump.setAttribute("aria-label", "回到最新消息");
  jump.hidden = true;
  main.append(jump);

  const messages = () => [...content.querySelectorAll('[data-testid="stChatMessage"]')];
  const anchor = () => {
    const composer = document.querySelector(".st-key-composer");
    if (!composer) return innerHeight - 24;
    const rect = composer.getBoundingClientRect();
    return rect.top - (innerWidth - rect.right < 52 ? 64 : 20);
  };
  const distance = (target = messages().at(-1)) => {
    return target ? target.getBoundingClientRect().bottom - anchor() : 0;
  };
  const later = (callback, delay) => {
    const timer = setTimeout(() => { timers.delete(timer); callback(); }, delay);
    timers.add(timer);
  };
  const align = (smooth = false, target = messages().at(-1), retry = false) => {
    if (!target) return;
    let delta = distance(target);
    const rect = target.getBoundingClientRect();
    if (retry && rect.bottom < 68) delta = rect.top - 68;
    else if (delta <= 0) return;
    programmatic = true;
    main.scrollTo({
      top: main.scrollTop + delta,
      behavior: smooth && !reduced.matches ? "smooth" : "instant",
    });
    later(() => { programmatic = false; view.top = main.scrollTop; }, smooth ? 450 : 60);
  };
  const release = () => {
    lastInput = performance.now();
    view.follow = false;
    schedule();
  };
  const refresh = () => {
    frame = 0;
    if (disposed) return;
    const nodes = messages();
    const grew = nodes.length > view.count;
    const status = content.querySelector(".generation-status");
    const busy = !!status;
    const active = status?.closest('[data-testid="stChatMessage"]');
    const settled = view.wasBusy && !busy;
    const freshGeneration = busy && status.dataset.generationKey !== view.generationKey;
    if (freshGeneration) {
      view.generationKey = status.dataset.generationKey;
      view.retryTarget = status.dataset.replacesAssistantId || null;
      view.retryPending = 0;
    }
    const retry = view.retryTarget && (active || content.querySelector(
      `.turn-anchor[data-assistant-id="${view.retryTarget}"]`
    )?.closest('[data-testid="stChatMessage"]'));
    const animate = node => {
      node.classList.add("wb-message-enter");
      node.addEventListener("animationend", () => node.classList.remove("wb-message-enter"), { once: true, signal });
    };
    if (grew && (existing || view.count > 0) && !reduced.matches) {
      nodes.slice(view.count).forEach(animate);
    }
    if (active && freshGeneration) {
      if (!reduced.matches && !grew) animate(active);
    }
    view.count = Math.max(view.count, nodes.length);
    view.wasBusy = busy;
    // Streamlit can remove the running node one frame before inserting the
    // saved node. Keep the target through that gap, never fall back to tail.
    if (view.follow && (!view.retryTarget || retry) && performance.now() - lastInput > 150 && (busy || grew || settled || retry)) {
      align(false, retry || nodes.at(-1), !!retry);
    }
    // One final alignment after the replacement mounts, then relinquish it.
    // A permanent saved anchor must never become a permanent scroll target.
    // A click-armed retry may finish before any .generation-status renders
    // (missing key, incompatible index). Hold the target briefly for that case.
    if (!busy && retry) {
      const wait = (view.retryPending || 0) - performance.now();
      if (wait > 0) later(schedule, wait + 20);
      else {
        view.retryTarget = null;
        view.retryPending = 0;
        // Parked on a middle turn: stop "following" so the jump button shows.
        view.follow = distance() < 40;
      }
    }
    jump.hidden = view.follow || !nodes.length || distance() < 40;
    const composer = document.querySelector(".st-key-composer");
    if (composer) {
      const rect = composer.getBoundingClientRect();
      const narrow = innerWidth - rect.right < 52;
      if (narrow) {
        if (!composer.contains(jumpDock)) composer.prepend(jumpDock);
        if (jump.parentElement !== jumpDock) jumpDock.append(jump);
        jumpDock.hidden = jump.hidden;
        jump.style.removeProperty("bottom");
        jump.style.removeProperty("left");
      } else {
        jumpDock.hidden = true;
        if (jump.parentElement !== main) main.append(jump);
        jump.style.bottom = `${innerHeight - rect.top + 12}px`;
        jump.style.left = `${rect.right + 26}px`;
      }
    }
  };
  function schedule() {
    if (!frame && !disposed) frame = requestAnimationFrame(refresh);
  }

  main.addEventListener("wheel", event => {
    detailsResize?.disconnect();
    if (event.deltaY < 0) release();
  }, { passive: true, signal });
  main.addEventListener("touchstart", event => {
    detailsResize?.disconnect();
    touchY = event.touches[0]?.clientY ?? null;
  }, { passive: true, signal });
  main.addEventListener("touchmove", event => {
    const y = event.touches[0]?.clientY;
    if (touchY !== null && y > touchY + 2) release();
    touchY = y ?? null;
  }, { passive: true, signal });
  main.addEventListener("scroll", () => {
    if (!programmatic && main.scrollTop < view.top - 2) release();
    if (!programmatic && distance() <= 40 && performance.now() - lastInput > 150) {
      view.follow = true;
      view.retryTarget = null;
      view.retryPending = 0;
    }
    view.top = main.scrollTop;
    schedule();
  }, { passive: true, signal });
  jump.addEventListener("click", () => {
    view.retryTarget = null;
    view.retryPending = 0;
    view.follow = true;
    lastInput = -Infinity;
    align(true);
    schedule();
  }, { signal });

  const beginFollow = retryButton => {
    view.follow = true;
    view.retryTarget = null;
    view.retryPending = 0;
    // The retry button's key carries the failed assistant id; arm the target now
    // so a fast failure (no .generation-status) cannot drag the view to the tail.
    const id = retryButton?.closest('[class*="st-key-retry_"]')
      ?.className.match(/st-key-retry_(\d+)/)?.[1];
    if (id) {
      view.retryTarget = id;
      view.retryPending = performance.now() + 1500;
    }
    lastInput = -Infinity;
  };
  document.addEventListener("click", event => {
    const button = event.target instanceof Element && event.target.closest(
      '[data-testid="stChatInputSubmitButton"], [class*="st-key-retry_"] button'
    );
    if (button && !button.disabled) beginFollow(button);
  }, { capture: true, signal });

  const revealDetails = details => {
    if (!details.isConnected || !details.open || disposed) return;
    const rect = details.getBoundingClientRect();
    const header = document.querySelector(".conversation-header");
    const top = Math.max(68, (header?.getBoundingClientRect().bottom || 44) + 20);
    const bottom = anchor();
    let delta = 0;
    if (rect.height > bottom - top) delta = rect.top - top;
    else if (rect.bottom > bottom) delta = rect.bottom - bottom;
    else if (rect.top < top) delta = rect.top - top;
    programmatic = true;
    main.scrollTop += delta;
    later(() => { programmatic = false; view.top = main.scrollTop; schedule(); }, 80);
  };
  document.addEventListener("click", event => {
    const summary = event.target instanceof Element && event.target.closest("summary");
    const details = summary?.closest("details");
    if (!details || !content.contains(details)) return;
    release();
    detailsResize?.disconnect();
    // Streamlit animates the expander's height. Follow its actual size, not an early timer.
    const expansion = new ResizeObserver(() => revealDetails(details));
    detailsResize = expansion;
    expansion.observe(details);
    later(() => expansion.disconnect(), 900);
  }, { signal });

  document.addEventListener("keydown", event => {
    const target = event.target;
    const input = target instanceof Element && target.matches('[data-testid="stChatInputTextArea"]');
    if (input && (event.isComposing || event.keyCode === 229) && ["Enter", "Escape"].includes(event.key)) {
      event.stopImmediatePropagation(); // Preserve IME default behavior; do not submit a candidate.
      return;
    }
    if (event.key === "Escape" && !event.isComposing) {
      const opener = document.querySelector('[data-testid="stPopoverButton"][aria-expanded="true"]');
      if (opener) {
        opener.click();
        opener.focus();
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      const cancel = input && document.querySelector(".st-key-cancel_failed_edit button");
      if (cancel) {
        cancel.click();
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
    }
    if (input && event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      beginFollow();
    }
    if (!input && ["PageUp", "Home", "ArrowUp"].includes(event.key)) release();
    if (event.key === "Escape" && target instanceof Element && target.closest(".st-key-prompt_surface") && !document.querySelector('[data-testid="stPopoverBody"]')) {
      const stop = document.querySelector(".st-key-stop_generation button:not(:disabled)");
      if (stop) { stop.click(); event.preventDefault(); }
    }
  }, { capture: true, signal });

  document.addEventListener("click", async event => {
    const button = event.target instanceof Element && event.target.closest("[data-copy-target]");
    if (!button || !/^answer_body_(?:unsaved_)?\d+$/.test(button.dataset.copyTarget)) return;
    const body = content.querySelector(`.st-key-${button.dataset.copyTarget}`);
    const label = button.querySelector("span");
    if (!body || !label) return;
    try {
      await navigator.clipboard.writeText(button.dataset.copyText ?? body.innerText.trim());
      if (disposed || !button.isConnected) return;
      label.textContent = "已复制";
      button.dataset.state = "copied";
    } catch {
      label.textContent = "复制失败，请手动选择";
      button.dataset.state = "error";
    }
    later(() => {
      if (button.isConnected) { label.textContent = "复制回答"; delete button.dataset.state; }
    }, 1800);
  }, { signal });

  const observer = new MutationObserver(schedule);
  observer.observe(content, { subtree: true, childList: true, characterData: true });
  const resize = new ResizeObserver(schedule);
  resize.observe(content);
  const composer = document.querySelector(".st-key-composer");
  if (composer) resize.observe(composer);
  window.addEventListener("resize", schedule, { signal });
  if (existing && !view.follow) main.scrollTop = view.top;
  schedule();
  if (scope.dataset.focus === "true") {
    later(() => content.querySelector('[data-testid="stChatInputTextArea"]:not(:disabled)')?.focus({ preventScroll: true }), 0);
  }
  window.__ragWorkbenchInteraction = {
    dispose() {
      disposed = true;
      controller.abort();
      observer.disconnect();
      resize.disconnect();
      detailsResize?.disconnect();
      cancelAnimationFrame(frame);
      timers.forEach(clearTimeout);
      jump.remove();
      jumpDock.remove();
    },
  };
})();
