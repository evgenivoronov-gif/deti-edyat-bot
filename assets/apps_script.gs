// Google Apps Script — журнал заявок бота ДЕТИ ЕДЯТ.
// Установка: Google Sheet → Extensions → Apps Script → вставить этот код → Deploy → Web app.

function doPost(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var data = JSON.parse(e.postData.contents);

  var headers = ["ID чата", "Начало заявки", "Обновлено", "Статус", "Кол-во детей", "Учреждение", "Название учреждения", "Питание", "Адрес", "Имя", "Телефон", "Telegram", "Канал"];
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(headers);
  }

  var chatId = String(data.chat_id);
  var now = new Date();
  var lastRow = sheet.getLastRow();
  var chatIdColumn = lastRow > 1 ? sheet.getRange(2, 1, lastRow - 1, 1).getValues() : [];

  var rowIndex = -1;
  for (var i = 0; i < chatIdColumn.length; i++) {
    if (String(chatIdColumn[i][0]) === chatId) {
      rowIndex = i + 2; // +2: с учётом заголовка и 1-based индексации
      break;
    }
  }

  var rowValues = [
    chatId,
    rowIndex === -1 ? now : sheet.getRange(rowIndex, 2).getValue(),
    now,
    data.status || "",
    data.kids_count || "",
    data.institution || "",
    data.institution_name || "",
    data.meals || "",
    data.address || "",
    data.name || "",
    data.phone || "",
    data.username || "",
    data.channel || "Telegram",
  ];

  if (rowIndex === -1) {
    sheet.appendRow(rowValues);
  } else {
    sheet.getRange(rowIndex, 1, 1, rowValues.length).setValues([rowValues]);
  }

  return ContentService.createTextOutput(JSON.stringify({ ok: true }))
    .setMimeType(ContentService.MimeType.JSON);
}
