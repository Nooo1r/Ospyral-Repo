document.addEventListener('DOMContentLoaded', () => {
  // ----------------- Элементы страницы -----------------
  const pagesContainer = document.getElementById('pages-container');
  const totalForms     = document.querySelector('input[name="pages-TOTAL_FORMS"]');
  const template       = document.getElementById('page-tile-template').content;
  const addBtn         = document.getElementById('add-page');

  // ------------ Элементы модалки цензуры ------------
  const modal     = document.getElementById('censor-modal');
  const cancelBtn = document.getElementById('cancel-censor');
  const saveBtn   = document.getElementById('save-censor');
  const undoBtn   = document.getElementById('undo-censor');
  const brushType = document.getElementById('brush-type');
  const brushSize = document.getElementById('brush-size');
  const canvas    = document.getElementById('censor-canvas');

  // ------------ Переменные для рисования ------------
  let ctx;                          // Контекст видимого canvas
  let offCanvas, offCtx;            // Скрытый canvas для хранения оригинала
  let drawing = false;
  let currentBrush = 'pixel';
  let brushW = parseInt(brushSize.value, 10);
  let undoStack = [];               // Массив снимков canvas для undo
  let activePageId = null;          // Текущий page_id в модалке

  // ------------ Утилиты ------------

  // Получить CSRF-токен из куки
  function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
      const cookies = document.cookie.split(';');
      for (let i = 0; i < cookies.length; i++) {
        const cookie = cookies[i].trim();
        if (cookie.substring(0, name.length + 1) === (name + '=')) {
          cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
          break;
        }
      }
    }
    return cookieValue;
  }

  // Показать или скрыть кнопки «Цензура» и «Пропустить»
  function toggleCensorButtons(tile, visible) {
    const censorBtn = tile.querySelector('.btn-censor');
    const skipBtn   = tile.querySelector('.btn-skip-censor');
    if (censorBtn) censorBtn.hidden = !visible;
    if (skipBtn)   skipBtn.hidden   = !visible;
  }

  // ------------ Открыть модалку цензуры ------------
  function openModal(imageUrl, imgType) {
    modal.hidden = false;

    // Создаём и настраиваем offCanvas (оригинал) и видимый canvas
    const img = new Image();
    img.src = imageUrl;
    img.onload = () => {
      // Задаём размеры канвасов по изображению
      canvas.width = img.width;
      canvas.height = img.height;

      // Настраиваем offCanvas и копируем туда оригинал
      offCanvas = document.createElement('canvas');
      offCanvas.width = img.width;
      offCanvas.height = img.height;
      offCtx = offCanvas.getContext('2d');
      offCtx.drawImage(img, 0, 0);

      // Настраиваем видимый canvas
      ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0);

      // Сохраняем начальное состояние для undo
      undoStack = [canvas.toDataURL()];
    };

    // Навешиваем события рисования
    canvas.addEventListener('mousedown', startDrawing);
    canvas.addEventListener('mousemove', draw);
    canvas.addEventListener('mouseup', stopDrawing);
    canvas.addEventListener('mouseout', stopDrawing);
  }

  function startDrawing(e) {
    drawing = true;
  }

  function draw(e) {
    if (!drawing) return;

    // Координаты кисти
    const rect = canvas.getBoundingClientRect();
    const x = Math.floor(e.clientX - rect.left - brushW / 2);
    const y = Math.floor(e.clientY - rect.top - brushW / 2);

    // Границы области
    const sx = Math.max(0, x);
    const sy = Math.max(0, y);
    const sw = Math.min(brushW, canvas.width - sx);
    const sh = Math.min(brushW, canvas.height - sy);

    if (sw <= 0 || sh <= 0) return;

    if (currentBrush === 'pixel') {
      // Пикселизация: берем блок из offCtx, вычисляем средний цвет и заливаем прямоугольник
      const imgData = offCtx.getImageData(sx, sy, sw, sh).data;
      let r = 0, g = 0, b = 0;
      const len = imgData.length / 4;
      for (let i = 0; i < imgData.length; i += 4) {
        r += imgData[i];
        g += imgData[i + 1];
        b += imgData[i + 2];
      }
      r = Math.round(r / len);
      g = Math.round(g / len);
      b = Math.round(b / len);

      ctx.fillStyle = `rgb(${r},${g},${b})`;
      ctx.fillRect(sx, sy, sw, sh);

    } else {
      // Размытие: копируем блок в tempCanvas, применяем фильтр и рисуем обратно
      const temp = document.createElement('canvas');
      temp.width = sw;
      temp.height = sh;
      const tctx = temp.getContext('2d');

      // Копируем нужную область с offCanvas
      tctx.drawImage(offCanvas, sx, sy, sw, sh, 0, 0, sw, sh);

      // Применяем фильтр blur
      tctx.filter = `blur(${Math.max(1, Math.floor(brushW / 5))}px)`;
      tctx.drawImage(temp, 0, 0);

      // Рисуем размытый блок обратно в видимый canvas
      ctx.drawImage(temp, 0, 0, sw, sh, sx, sy, sw, sh);
      tctx.filter = 'none';
    }

    // Сохраняем текущее состояние для undo
    undoStack.push(canvas.toDataURL());
  }

  function stopDrawing() {
    if (!drawing) return;
    drawing = false;
  }

  // ------------ Кнопка «Отмена последнего» ------------
  undoBtn.addEventListener('click', () => {
    if (undoStack.length <= 1) return;
    undoStack.pop();
    const prevData = undoStack[undoStack.length - 1];
    const img = new Image();
    img.src = prevData;
    img.onload = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0);
    };
  });

  // ------------ Смена типа кисти и размера ------------
  brushType.addEventListener('change', () => {
    currentBrush = brushType.value;
  });
  brushSize.addEventListener('input', () => {
    brushW = parseInt(brushSize.value, 10);
  });

  // ------------ Отмена модалки (не сохраняем цензуру) ------------
  cancelBtn.addEventListener('click', () => {
    modal.hidden = true;
    cleanupCanvasListeners();
  });

  // ------------ Сохранить отретушированное изображение ------------
  saveBtn.addEventListener('click', () => {
    const dataURL = canvas.toDataURL();
    fetch(`/artwork/page/${activePageId}/save/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-CSRFToken': getCookie('csrftoken')
      },
      body: `editedImage=${encodeURIComponent(dataURL)}`
    })
    .then(res => res.json())
    .then(data => {
      if (data.success) {
        const tile = document.querySelector(`.page-tile[data-page-id="${activePageId}"]`);
        const thumb = tile.querySelector('.page-thumb');
        thumb.src = data.url;
        thumb.setAttribute('data-img-type', 'censored');
      } else {
        console.error('Ошибка save_censored_page:', data.errors);
      }
      modal.hidden = true;
      cleanupCanvasListeners();
    })
    .catch(err => {
      console.error(err);
      modal.hidden = true;
      cleanupCanvasListeners();
    });
  });

  function cleanupCanvasListeners() {
    canvas.removeEventListener('mousedown', startDrawing);
    canvas.removeEventListener('mousemove', draw);
    canvas.removeEventListener('mouseup', stopDrawing);
    canvas.removeEventListener('mouseout', stopDrawing);
  }

  // ------------ Инициализация одного тайла ------------
  function initTile(tile) {
    const fileInput = tile.querySelector('input[type="file"]');
    const dropZone  = tile.querySelector('.page-drop-zone');
    const removeBtn = tile.querySelector('.btn-remove-page');
    const censorBtn = tile.querySelector('.btn-censor');
    const skipBtn   = tile.querySelector('.btn-skip-censor');

    // Показываем или прячем кнопки «Цензура» / «Пропустить» при инициализации
    const existingThumb = tile.querySelector('.page-thumb');
    const hasOriginalUrl = Boolean(tile.dataset.originalUrl);
    if (existingThumb || hasOriginalUrl) {
      if (censorBtn) censorBtn.hidden = false;
      if (skipBtn)   skipBtn.hidden   = false;
    } else {
      if (censorBtn) censorBtn.hidden = true;
      if (skipBtn)   skipBtn.hidden   = true;
    }

    // 1) Drag&drop и клик по зоне для загрузки файла
    if (dropZone && fileInput) {
      dropZone.addEventListener('click', () => fileInput.click());
      dropZone.addEventListener('dragover', e => {
        e.preventDefault();
        tile.classList.add('dragover');
      });
      dropZone.addEventListener('dragleave', () => tile.classList.remove('dragover'));
      dropZone.addEventListener('drop', e => {
        e.preventDefault();
        tile.classList.remove('dragover');
        if (e.dataTransfer.files.length) {
          fileInput.files = e.dataTransfer.files;
          handleFileSelection(e.dataTransfer.files[0], tile);
        }
      });
      fileInput.addEventListener('change', () => {
        if (fileInput.files.length) {
          handleFileSelection(fileInput.files[0], tile);
        }
      });
    }

    // 2) Кнопка «Пропустить цензуру»
    if (skipBtn) {
      skipBtn.addEventListener('click', () => {
        const pageId = tile.dataset.pageId;
        if (!pageId) {
          alert('Сначала сохраните артворк, чтобы пропустить цензуру для этой страницы.');
          return;
        }
        if (censorBtn) censorBtn.hidden = true;
        skipBtn.hidden = true;
      });
    }

    // 3) Кнопка «Цензура»
    if (censorBtn) {
      censorBtn.addEventListener('click', () => {
        const pageId = tile.dataset.pageId;
        if (!pageId) {
          alert('Сначала сохраните артворк, чтобы цензурить эту страницу.');
          return;
        }
        const thumb = tile.querySelector('.page-thumb');
        const imgUrl = thumb.src;
        activePageId = pageId;
        openModal(imgUrl);
      });
    }

    // 4) Кнопка удаления страницы
    if (removeBtn) {
      removeBtn.addEventListener('click', () => {
        const pageId = tile.dataset.pageId;
        if (pageId) {
          fetch(`/artwork/page/${pageId}/delete/`, {
            method: 'POST',
            headers: { 'X-CSRFToken': getCookie('csrftoken') }
          })
          .then(res => res.json())
          .then(data => {
            if (data.success) {
              tile.remove();
              reindexOrder();
            } else {
              alert('Ошибка при удалении страницы.');
            }
          })
          .catch(err => {
            console.error(err);
            alert('Сетевая ошибка при удалении страницы');
          });
        } else {
          tile.remove();
          reindexOrder();
        }
      });
    }
  }

  // ------------ Инициализация всех тайлов при загрузке ------------
  function initTiles(tiles) {
    tiles.forEach(t => initTile(t));
    reindexOrder();
  }

  // ------------ Обработка выбора файла и AJAX-загрузка ------------
  function handleFileSelection(file, tile) {
    let thumb = tile.querySelector('.page-thumb');
    if (!thumb) {
      thumb = document.createElement('img');
      thumb.classList.add('page-thumb');
      thumb.setAttribute('data-img-type', 'original');
      tile.querySelector('.page-drop-zone').innerHTML = "";
      tile.querySelector('.page-drop-zone').append(thumb);
    }

    // Локальный preview
    thumb.src = URL.createObjectURL(file);
    thumb.setAttribute('data-img-type', 'original');

    // Показать кнопки «Цензура» / «Пропустить» после локального preview
    const localCensorBtn = tile.querySelector('.btn-censor');
    const localSkipBtn   = tile.querySelector('.btn-skip-censor');
    if (localCensorBtn) localCensorBtn.hidden = false;
    if (localSkipBtn)   localSkipBtn.hidden   = false;

    // Если артворк ещё не сохранён, отменяем AJAX
    const artId = "{{ art_form.instance.id|default:'' }}";
    if (!artId) {
      return;
    }

    // Собираем данные и отправляем AJAX-запрос на создание страницы
    const prefixIndex = tile.dataset.prefix;
    const formData = new FormData();
    formData.append('image', file);
    formData.append('order', prefixIndex);
    formData.append('artwork', artId);

    fetch("{% url 'create_artwork_page' %}", {
      method: 'POST',
      headers: { 'X-CSRFToken': getCookie('csrftoken') },
      body: formData
    })
    .then(res => res.json())
    .then(data => {
      if (data.success) {
        tile.dataset.pageId      = data.page_id;
        tile.dataset.originalUrl = data.url;
        thumb.src = data.url;

        // Показываем кнопки «Цензура» и «Пропустить»
        tile.querySelector('.btn-censor').hidden     = false;
        tile.querySelector('.btn-skip-censor').hidden = false;
      } else {
        console.error('Ошибка create_artwork_page:', data.errors);
      }
    })
    .catch(err => console.error(err));
  }

  // ------------ Пересчёт порядковых индексов и полей formset ------------
  function reindexOrder() {
    Array.from(pagesContainer.children).forEach((tile, idx) => {
      const orderIn = tile.querySelector(`input[name^="pages-"][name$="-order"]`);
      if (orderIn) orderIn.value = idx;

      const fileIn = tile.querySelector('input[type="file"]');
      if (fileIn) {
        const newName = fileIn.name.replace(/pages-\d+-/, `pages-${idx}-`);
        fileIn.name = newName;
        fileIn.id   = 'id_' + newName;
      }

      const delIn = tile.querySelector(`input[name^="pages-"][name$="-DELETE"]`);
      if (delIn) {
        delIn.name = `pages-${idx}-DELETE`;
      }
      tile.dataset.prefix = idx;
    });
    totalForms.value = pagesContainer.children.length;
  }

  // ------------ Добавить новую страницу ------------
  addBtn.addEventListener('click', () => {
    const count = parseInt(totalForms.value, 10);

    // Клонируем шаблон
    const newTile = document.importNode(template, true);
    newTile.querySelector('.page-tile').dataset.pageId      = "";
    newTile.querySelector('.page-tile').dataset.originalUrl = "";

    // Заменяем __prefix__ на фактический индекс
    const html = newTile.querySelector('.page-tile').innerHTML.replace(/__prefix__/g, count);
    newTile.querySelector('.page-tile').innerHTML = html;

    pagesContainer.appendChild(newTile);
    totalForms.value = count + 1;

    initTile(newTile.querySelector('.page-tile'));
    reindexOrder();
  });

  // Инициализируем существующие тайлы
  initTiles(pagesContainer.querySelectorAll('.page-tile'));
});
