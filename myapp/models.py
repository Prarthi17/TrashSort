from django.db import models


class ContactMessage(models.Model):
    name = models.CharField(max_length=120)
    email = models.EmailField()
    subject = models.CharField(max_length=200)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Contact from {self.name}: {self.subject}"


class Feedback(models.Model):
    name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    rating = models.IntegerField(choices=[(i, str(i)) for i in range(1, 6)])
    comments = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Feedback {self.rating}/5 by {self.name}"
