$(document).ready(function() {
    var loginUrl = window.APP_CONFIG.loginUrl;
    var registerUrl = window.APP_CONFIG.registerUrl;
    var modalOpen = false; // Флаг, указывающий, что модальное окно открыто

    // Открытие модального окна логина
    $('#open-login-modal').on('click', function(e) {
        e.preventDefault();
        $.ajax({
            url: loginUrl,
            type: "GET",
            success: function(data) {
                $('#login-form-container').html(data);
                $('#login-modal').fadeIn(function() {
                    modalOpen = true;
                    $("body").addClass("modal-open");
                });
            },
            error: function() {
                alert("Ошибка загрузки формы логина.");
            }
        });
    });

    // Открытие модального окна регистрации
    $('#open-register-modal').on('click', function(e) {
        e.preventDefault();
        $.ajax({
            url: registerUrl,
            type: "GET",
            success: function(data) {
                $('#register-form-container').html(data);
                $('#register-modal').fadeIn(function() {
                    modalOpen = true;
                    $("body").addClass("modal-open");
                });
            },
            error: function() {
                alert("Ошибка загрузки формы регистрации.");
            }
        });
    });

    // Закрытие модальных окон при клике на крестик
    $('.close-modal').on('click', function() {
        $('#login-modal, #register-modal').fadeOut(function() {
            modalOpen = false;
            $("body").removeClass("modal-open");
        });
    });

    // Закрытие модальных окон при клике вне их содержимого
    $(window).on('click', function(e) {
        if ($(e.target).is('#login-modal, #register-modal')) {
            $('#login-modal, #register-modal').fadeOut(function() {
                modalOpen = false;
                $("body").removeClass("modal-open");
            });
        }
    });
})

