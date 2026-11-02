from rest_framework import serializers

from .models import Event


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = ["id", "scope", "title", "date_start", "date_end", "announced_at",
                  "date_text", "access", "source", "url", "note", "is_published",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        start, end = attrs.get("date_start"), attrs.get("date_end")
        if start and end and end < start:
            raise serializers.ValidationError({"date_end": "Дата окончания раньше даты начала."})
        return attrs


class EventImportRequestSerializer(serializers.Serializer):
    schema_version = serializers.IntegerField()
    year = serializers.IntegerField(required=False)
    events = serializers.ListField(child=serializers.DictField())


class EventImportResultSerializer(serializers.Serializer):
    created = serializers.IntegerField()
    updated = serializers.IntegerField()
    skipped = serializers.IntegerField()
