document.addEventListener("DOMContentLoaded", function() {
    var template = document.getElementById('template');
    var body = document.getElementById('results');
    var params = new URLSearchParams(window.location.search);
    var q = params.get('q');
    if (!q) {
        return;
    }
    var jss = new JsSearch.Search('title');
    jss.tokenizer =	new JsSearch.StopWordsTokenizer(new JsSearch.SimpleTokenizer());
    jss.addIndex('title');
    jss.addIndex('text');
    jss.addDocuments(docs);
    var results = jss.search(q);
    var summary = document.getElementsByClassName('summary')[0];
    summary.innerHTML = results.length + ' results found for "<i>' + q + '</i>"';
    var words = q.split(' ');
    for (var i = 0; i < results.length; i++) {
      var result = results[i];
      var clone = template.cloneNode(true);
      clone.removeAttribute('id');
      clone.classList.add("result-" + result.type);
      var title = clone.childNodes[0];
      var icon = title.childNodes[0];
      var link = title.childNodes[1];
      var text = result.text;
      var title = result.title;
      if (text.length > 200) {
          text = text.slice(0, 210);
          var n = text.lastIndexOf(' ');
          if (n >= 0) {
            text = text.slice(0, n);
          }
          text = text + '&nbsp;...';
      }
      // Bold search terms from text
      for (var j = 0; j < words.length; j++) {
          text = text.replace(new RegExp('(' + words[j] + ')', 'i'), '<b>$1</b>');
          title = title.replace(new RegExp('(' + words[j] + ')', 'i'), '<b>$1</b>');
      }
      icon.setAttribute('title', result.type);
      link.setAttribute('href', result.path);
      link.innerHTML = title + (result.type == 'function' ? "()" : "");
      var desc = clone.childNodes[1];
      desc.innerHTML = text;
      body.appendChild(clone);
    }
  });