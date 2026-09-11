// Keep the selected navigation item visible with an explicit way to expand its group.
(function () {
  function initialize() {
    document.querySelectorAll('nav.tabs, [data-tab-strip], .qnav-main').forEach(function (group) {
      var links = Array.from(group.children).filter(function (el) { return el.tagName === 'A' && el.hasAttribute('href'); });
      if (links.length < 2) return;
      var active = links.find(function (link) { return link.href === window.location.href; }) || links.find(function (link) {
        return link.classList.contains('active') || link.classList.contains('primary') || link.classList.contains('is-active');
      });
      var others = Array.from(group.children);
      var original = new Map();
      var toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'btn ghost';
      toggle.textContent = document.documentElement.lang === 'en' ? 'Show all tabs' : 'إظهار الكل';
      toggle.hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
      group.appendChild(toggle);
      function collapse(selected) {
        others.forEach(function (item) {
          if (item === selected) return;
          if (!original.has(item)) original.set(item, [item.style.getPropertyValue('display'), item.style.getPropertyPriority('display')]);
          item.style.setProperty('display', 'none', 'important');
        });
        toggle.hidden = false;
        toggle.setAttribute('aria-expanded', 'false');
      }
      toggle.addEventListener('click', function () {
        original.forEach(function (value, item) {
          if (value[0]) item.style.setProperty('display', value[0], value[1]);
          else item.style.removeProperty('display');
        });
        original.clear();
        toggle.hidden = true;
        toggle.setAttribute('aria-expanded', 'true');
        if (active) active.focus();
      });
      links.forEach(function (link) {
        link.addEventListener('click', function (event) {
          if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0 || link.target === '_blank') return;
          active = link;
          collapse(link);
        });
      });
      if (active) collapse(active);
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
  else initialize();
})();
